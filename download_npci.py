#!/usr/bin/env python3
"""
download_npci.py - Click through month/year on NPCI's P2P & P2M tab
and save every monthly xlsx into ./npci_raw/.

Setup: pip install playwright && playwright install chromium
Run:   python download_npci.py --start 2019 --end 2026
Already-downloaded months are skipped, so you can safely rerun.
"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://www.npci.org.in/product/ecosystem-statistics/upi"
TAB = "P2P and P2M Transactions"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RAW = Path("npci_raw")


def pick(page, current_text, target):
    """Open a dropdown showing `current_text` and choose `target`."""
    page.get_by_text(current_text, exact=True).first.click()
    page.wait_for_timeout(600)
    page.get_by_text(target, exact=True).last.click()
    page.wait_for_timeout(1500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2019)
    ap.add_argument("--end", type=int, default=2026)
    a = ap.parse_args()
    RAW.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(accept_downloads=True)
        page.goto(URL, wait_until="networkidle", timeout=90_000)
        page.get_by_text(TAB, exact=True).first.click()
        page.wait_for_timeout(2500)

        # read what the dropdowns currently show (e.g. "Aug", "2026")
        cur_m = next(m for m in MONTHS
                     if page.get_by_text(m, exact=True).count())
        cur_y = str(a.end)
        for y in range(a.end, a.start - 1, -1):
            if page.get_by_text(str(y), exact=True).count():
                cur_y = str(y)
                break

        for y in range(a.start, a.end + 1):
            for m in MONTHS:
                out = RAW / f"{y}-{MONTHS.index(m) + 1:02d}.xlsx"
                if out.exists():
                    continue
                try:
                    if str(y) != cur_y:
                        pick(page, cur_y, str(y)); cur_y = str(y)
                    if m != cur_m:
                        pick(page, cur_m, m); cur_m = m
                    if page.get_by_text("No data found").count():
                        # try archived view once
                        btn = page.get_by_text("Show archived")
                        if btn.count():
                            btn.first.click(); page.wait_for_timeout(1500)
                    if page.get_by_text("No data found").count():
                        print(f"  {y}-{m}: no data"); continue
                    with page.expect_download(timeout=15_000) as d:
                        page.get_by_text("Download").first.click()
                    d.value.save_as(out)
                    print(f"  {y}-{m}: saved")
                except Exception as e:
                    print(f"  {y}-{m}: FAILED ({str(e)[:80]})")
        browser.close()
    print("Done. Now run: python merge_npci.py")


if __name__ == "__main__":
    main()
