#!/usr/bin/env python3
"""
collect_data.py - Gather real inputs for the synthetic daily UPI series.

Outputs (in ./data/):
  upi_monthly.csv   month, volume_mn, value_cr, days_in_month, avg_daily_volume_mn
  holidays.csv      Prophet-ready: holiday, ds, lower_window, upper_window
  events.csv        one-off structural shocks (demonetisation, COVID, ...)

Usage:
  pip install pandas lxml requests holidays
  python collect_data.py                      # scrape NPCI live
  python collect_data.py --html npci.html     # parse a manually saved page
  python collect_data.py --start 2019 --end 2026
"""
import argparse
import sys
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import holidays as hol

NPCI_URL = "https://www.npci.org.in/what-we-do/upi/product-statistics"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
OUT = Path("data")


# ----------------------------------------------------------------- NPCI
def fetch_html(url: str, retries: int = 3) -> str:
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  attempt {i + 1} failed: {e}")
            time.sleep(2 * (i + 1))
    raise RuntimeError("Could not fetch NPCI page.")


def _find(cols, *keys):
    for c in cols:
        if all(k in c.lower() for k in keys):
            return c
    return None


def parse_npci(html: str) -> pd.DataFrame:
    tables = pd.read_html(StringIO(html))
    frames = []
    for t in tables:
        t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c)
                     for c in t.columns]
        mcol = _find(t.columns, "month")
        vcol = _find(t.columns, "volume")
        valc = _find(t.columns, "value")
        if not (mcol and vcol):
            continue
        f = pd.DataFrame({
            "month": pd.to_datetime(t[mcol].astype(str).str.strip(),
                                    format="mixed", errors="coerce"),
            "volume_mn": pd.to_numeric(
                t[vcol].astype(str).str.replace(",", ""), errors="coerce"),
        })
        if valc:
            f["value_cr"] = pd.to_numeric(
                t[valc].astype(str).str.replace(",", ""), errors="coerce")
        frames.append(f.dropna(subset=["month", "volume_mn"]))
    if not frames:
        raise ValueError("No table with Month/Volume columns found. "
                         "NPCI may have changed its layout.")
    df = pd.concat(frames).drop_duplicates("month").sort_values("month")
    df["month"] = df["month"].dt.to_period("M").dt.to_timestamp()
    df["days_in_month"] = df["month"].dt.days_in_month
    df["avg_daily_volume_mn"] = df["volume_mn"] / df["days_in_month"]
    return df.reset_index(drop=True)


def sanity_check(df: pd.DataFrame):
    gaps = pd.date_range(df.month.min(), df.month.max(), freq="MS") \
        .difference(df.month)
    if len(gaps):
        print(f"  WARNING: {len(gaps)} missing months: "
              f"{[d.strftime('%Y-%m') for d in gaps[:10]]}")
    mom = df.volume_mn.pct_change()
    odd = df[(mom > 0.5) | (mom < -0.3)]
    if len(odd):
        print("  WARNING: suspicious month-on-month jumps (check units):")
        print(odd[["month", "volume_mn"]].to_string(index=False))


# ------------------------------------------------------------- calendar
# Lunar festivals: VERIFY against a panchang before final submission.
FESTIVALS = {
    "diwali": (["2019-10-27", "2020-11-14", "2021-11-04", "2022-10-24",
                "2023-11-12", "2024-10-31", "2025-10-20", "2026-11-08"],
               -10, 3),     # festive shopping ramp before, dip after
    "holi":   (["2019-03-21", "2020-03-10", "2021-03-29", "2022-03-18",
                "2023-03-08", "2024-03-25", "2025-03-14", "2026-03-04"],
               -2, 1),
}

EVENTS = [  # name, start, lower_window, upper_window
    ("demonetisation", "2016-11-08", 0, 60),
    ("covid_lockdown", "2020-03-25", 0, 75),
    ("covid_wave2",    "2021-04-15", 0, 45),
    ("upi_lite_launch", "2022-09-20", 0, 0),
]


def build_holidays(start: int, end: int) -> pd.DataFrame:
    ind = hol.India(years=range(start, end + 1))
    base = pd.DataFrame(
        [(name, pd.Timestamp(d)) for d, name in sorted(ind.items())],
        columns=["holiday", "ds"])
    base["lower_window"], base["upper_window"] = 0, 0
    # drop generic entries that the custom windows replace
    base = base[~base.holiday.str.contains("Diwali|Deepavali|Holi",
                                           case=False)]
    rows = []
    for name, (dates, lo, hi) in FESTIVALS.items():
        for d in dates:
            if start <= int(d[:4]) <= end:
                rows.append((name, pd.Timestamp(d), lo, hi))
    fest = pd.DataFrame(rows, columns=base.columns)
    return pd.concat([base, fest]).sort_values("ds").reset_index(drop=True)


def build_events() -> pd.DataFrame:
    e = pd.DataFrame(EVENTS,
                     columns=["holiday", "ds", "lower_window", "upper_window"])
    e["ds"] = pd.to_datetime(e["ds"])
    return e


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="parse a saved NPCI page instead of fetching")
    ap.add_argument("--start", type=int, default=2019)
    ap.add_argument("--end", type=int, default=2026)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    print("[1/3] UPI monthly statistics")
    try:
        html = Path(a.html).read_text(encoding="utf-8") if a.html \
            else fetch_html(NPCI_URL)
        upi = parse_npci(html)
    except Exception as e:
        print(f"  FAILED: {e}\n  Fallback: open {NPCI_URL} in a browser, "
              f"save the page (Ctrl+S) and rerun with --html <file>.")
        sys.exit(1)
    upi = upi[upi.month.dt.year.between(a.start, a.end)]
    sanity_check(upi)
    upi.to_csv(OUT / "upi_monthly.csv", index=False)
    print(f"  {len(upi)} months, {upi.month.min():%Y-%m} to "
          f"{upi.month.max():%Y-%m} -> data/upi_monthly.csv")

    print("[2/3] Holiday calendar")
    h = build_holidays(a.start, a.end)
    h.to_csv(OUT / "holidays.csv", index=False)
    print(f"  {len(h)} entries -> data/holidays.csv")

    print("[3/3] Structural events")
    build_events().to_csv(OUT / "events.csv", index=False)
    print("  -> data/events.csv")
    print("Done.")


if __name__ == "__main__":
    main()
