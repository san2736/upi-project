# Prophet Model Implementation for Digital Payment Volume Forecasting with Holiday and Event Effects

Time Series (ATSA) group project. Forecasts daily UPI transaction volume with Prophet and tests:

- whether modelling Indian festivals, national holidays and salary-cycle effects improves forecasts;
- whether Prophet recovers the true size of those effects.

> **The daily data is synthetic.** NPCI publishes UPI statistics monthly only. A daily series with known calendar
> effects is generated from the real NPCI monthly totals: every month matches NPCI exactly except five festive months.
> See [`DATA.md`](DATA.md) for the method and its limitations.

## Repository layout

```
upi-project/
├── README.md
├── DATA.md                        data collection and synthetic generation (method + limitations)
├── scrape_npci.py                 collects real NPCI monthly UPI statistics (Playwright)
├── generate_synthetic.py          builds the synthetic daily series (v2, seed 1)
└── data/
    ├── upi_monthly.csv            real NPCI monthly totals, Jan 2022 – Aug 2026
    ├── holidays.csv               gazetted national holiday list (single days)
    ├── synthetic_daily.csv        synthetic daily series + ground-truth effect columns
    ├── prophet_holidays.csv       known events in Prophet format (hidden sale excluded)
    ├── truth.json                 every injected parameter and the festive-month deviations
    ├── synthetic_check.png        visual check of the generated series
    └── archive/
        └── synthetic_daily_v1.csv daily series from generator v1, kept for comparison
```

## How to regenerate the data

```bash
pip install numpy pandas scipy matplotlib
python scrape_npci.py --start 2022 --end 2026   # real monthly data (needs Playwright + Chrome)
python generate_synthetic.py                    # synthetic daily data
```

`scipy` is required. Without it, the trend interpolation falls back to linear and the generated data changes.

## Changelog

- **Generator v2:** festive months (Oct 2022, Oct 2023, Nov 2023, Oct 2024, Oct 2025) are no longer forced to match
  NPCI. In v1, anchoring them pushed ordinary festive-month days about 5% below trend and hid about half of the Diwali
  effect. See `DATA.md` §3.4.
