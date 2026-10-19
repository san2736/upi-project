#!/usr/bin/env python3
"""
merge_npci.py - Merge monthly NPCI "P2P and P2M Transactions" xlsx files.

Put all downloaded files in ./npci_raw/ then run:
  python merge_npci.py
Output: data/upi_monthly.csv with columns
  month, volume_mn, value_cr, p2p_volume_mn, p2p_value_cr,
  p2m_volume_mn, p2m_value_cr, days_in_month, avg_daily_volume_mn
"""
from pathlib import Path
import pandas as pd

RAW, OUT = Path("npci_raw"), Path("data")
COLS = ["volume_mn", "value_cr", "p2p_volume_mn", "p2p_value_cr",
        "p2m_volume_mn", "p2m_value_cr"]


def num(x):
    return float(str(x).replace(",", "").strip())


def parse(path):
    df = pd.read_excel(path, header=None)
    rows = []
    for _, r in df.iterrows():
        m = pd.to_datetime(str(r[0]).strip(), format="%b-%y", errors="coerce")
        if pd.notna(m):
            rows.append([m] + [num(r[i]) for i in range(1, 7)])
    return rows


def main():
    files = sorted(RAW.glob("*.xls*"))
    if not files:
        raise SystemExit(f"No xlsx files in {RAW}/")
    rows = []
    for f in files:
        try:
            got = parse(f)
            rows += got
            if not got:
                print(f"  no data row in {f.name}")
        except Exception as e:
            print(f"  skipped {f.name}: {e}")

    df = pd.DataFrame(rows, columns=["month"] + COLS)
    df = df.drop_duplicates("month").sort_values("month").reset_index(drop=True)
    df["days_in_month"] = df.month.dt.days_in_month
    df["avg_daily_volume_mn"] = df.volume_mn / df.days_in_month

    # checks
    bad = (df.p2p_volume_mn + df.p2m_volume_mn - df.volume_mn).abs() > 1
    if bad.any():
        print("  WARNING: P2P+P2M != Total for:",
              df.loc[bad, "month"].dt.strftime("%Y-%m").tolist())
    missing = pd.date_range(df.month.min(), df.month.max(), freq="MS") \
        .difference(df.month)
    if len(missing):
        print("  Missing months:", [d.strftime("%Y-%m") for d in missing])

    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "upi_monthly.csv", index=False)
    print(f"{len(df)} months ({df.month.min():%Y-%m} to "
          f"{df.month.max():%Y-%m}) -> data/upi_monthly.csv")


if __name__ == "__main__":
    main()
