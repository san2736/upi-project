#!/usr/bin/env python3
"""
scrape_npci.py - Read NPCI UPI "P2P and P2M Transactions" month by month.
Pass 1: normal view (recent years).  Pass 2: "Show archived" view (older years).

Run:   python scrape_npci.py --start 2019 --end 2026
Output: data/upi_monthly.csv  (reruns resume where they left off)
"""
import argparse
from pathlib import Path
import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.npci.org.in/product/ecosystem-statistics/upi"
TAB = "P2P and P2M Transactions"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
OUT = Path("data/upi_monthly.csv")
COLS = ["volume_mn", "value_cr", "p2p_volume_mn", "p2p_value_cr",
        "p2m_volume_mn", "p2m_value_cr"]
DEBUG_DUMPED = False


def num(s):
    return float(s.replace(",", "").strip())


def dds(page):
    return page.locator(".ecosystem-stat-filters .ecosystem-stat-dropdown-black-arrow")


def options(page, dd):
    dd.locator("button.dropdown-toggle").click()
    page.wait_for_timeout(500)
    opts = [t.strip() for t in dd.locator("ul.dropdown-menu li").all_inner_texts()]
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    return [o for o in opts if o]


def pick(page, dd, label):
    dd.locator("button.dropdown-toggle").click()
    page.wait_for_timeout(500)
    item = dd.locator("ul.dropdown-menu li").filter(
        has_text=__import__("re").compile(rf"^\s*{label}\s*$")).first
    if not item.count():
        page.keyboard.press("Escape")
        return False
    item.click()
    return True


def read_rows(page):
    """All table rows whose first cell parses as a month like 'Aug-26'."""
    global DEBUG_DUMPED
    found = {}
    for r in page.locator("table tr").all():
        cells = [c.strip() for c in r.locator("th, td").all_inner_texts()]
        if len(cells) < 7:
            continue
        label = cells[0].replace("\u2019", "-").replace("'", "-").replace(" ", "-")
        m = pd.to_datetime(label, format="%b-%y", errors="coerce")
        if pd.isna(m):
            m = pd.to_datetime(label, format="%b-%Y", errors="coerce")
        if pd.isna(m):
            continue
        try:
            found[m] = [num(c) for c in cells[1:7]]
        except ValueError:
            pass
    if not found and not DEBUG_DUMPED:
        txt = page.locator("table").first.inner_text() if page.locator("table").count() else "(no <table>)"
        print("  DEBUG table text:\n" + txt[:600])
        DEBUG_DUMPED = True
    return found


def open_page(page, archived):
    for attempt in range(3):
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            page.get_by_text(TAB, exact=True).first.wait_for(timeout=30_000)
            break
        except Exception as e:
            print(f"  page load attempt {attempt + 1} failed, retrying...")
    else:
        raise RuntimeError("NPCI page would not load")
    page.get_by_text(TAB, exact=True).first.click()
    page.wait_for_timeout(2500)
    if archived:
        btn = page.get_by_text("Show archived")
        if btn.count():
            btn.first.click()
            page.wait_for_timeout(2500)
        else:
            print("  'Show archived' button not visible in this view")


def run_pass(page, archived, a, rows):
    open_page(page, archived)
    mdd, ydd = dds(page).nth(0), dds(page).nth(1)
    years = options(page, ydd)
    print(f"[{'archived' if archived else 'current'}] years offered: {years}")
    for y in range(a.start, a.end + 1):
        if str(y) not in years:
            continue
        if not pick(page, ydd, str(y)):
            print(f"  {y}: could not select"); continue
        page.wait_for_timeout(1500)
        for i, m in enumerate(MONTHS, 1):
            ts = pd.Timestamp(f"{y}-{i:02d}-01")
            if ts in rows:
                continue
            try:
                if not pick(page, mdd, m):
                    continue
                page.wait_for_timeout(1800)
                for mm, vals in read_rows(page).items():
                    rows[mm] = vals
                print(f"  {m}-{str(y)[2:]}: " +
                      (f"total {rows[ts][0]:,.2f} Mn" if ts in rows else "no data"))
                save(rows)
            except Exception as e:
                print(f"  {m}-{y}: FAILED ({str(e).splitlines()[0][:90]})")


def save(rows):
    if not rows:
        return None
    df = pd.DataFrame([[pd.Timestamp(k)] + list(v) for k, v in rows.items()], columns=["month"] + COLS)
    df = df.sort_values("month").reset_index(drop=True)
    df["days_in_month"] = df.month.dt.days_in_month
    df["avg_daily_volume_mn"] = df.volume_mn / df.days_in_month
    df.to_csv(OUT, index=False)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2019)
    ap.add_argument("--end", type=int, default=2026)
    ap.add_argument("--archived-only", action="store_true")
    a = ap.parse_args()
    OUT.parent.mkdir(exist_ok=True)

    rows = {}
    if OUT.exists():
        d = pd.read_csv(OUT, parse_dates=["month"])
        rows = {r.month: [getattr(r, c) for c in COLS] for r in d.itertuples()}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_default_timeout(10_000)
        if not a.archived_only:
            run_pass(page, False, a, rows)
        run_pass(page, True, a, rows)
        browser.close()

    df = save(rows)
    if df is None:
        print("No data collected. Paste the output (incl. DEBUG block) to Claude.")
        return
    bad = (df.p2p_volume_mn + df.p2m_volume_mn - df.volume_mn).abs() > 1
    if bad.any():
        print("WARNING P2P+P2M != Total:", df.month[bad].dt.strftime("%Y-%m").tolist())
    missing = pd.date_range(df.month.min(), df.month.max(), freq="MS").difference(df.month)
    if len(missing):
        print("Missing months:", [d.strftime("%Y-%m") for d in missing])
    print(f"{len(df)} months ({df.month.min():%Y-%m} to {df.month.max():%Y-%m}) -> {OUT}")


if __name__ == "__main__":
    main()
