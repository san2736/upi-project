# Data: Collection and Synthetic Generation

Project: *Prophet Model Implementation for Digital Payment Volume Forecasting with Holiday and Event Effects*

This document describes where the real data comes from, how it was collected, and how the synthetic daily series used for modelling is built from it. All daily data in this project is **synthetic**. Only the monthly totals are real.

---

## 1. Why synthetic daily data?

Prophet's holiday machinery needs daily observations: a festival effect such as Diwali lasts about two weeks and cannot be separated from ordinary yearly seasonality in monthly data. The National Payments Corporation of India (NPCI) publishes UPI statistics only at monthly granularity, and no public source provides daily UPI volumes.

The approach taken is therefore to generate a daily series whose calendar effects are **known by construction**, while forcing every month to sum exactly to the real NPCI total. This has two advantages:

1. The series has realistic level and growth, because it is anchored to real data.
2. Every holiday, weekday and payday effect has a known true value, so we can measure whether Prophet recovers it, which is not possible with real data.

---

## 2. Real data collected

### 2.1 Source

| Item | Detail |
|---|---|
| Publisher | National Payments Corporation of India (NPCI) |
| Page | https://www.npci.org.in/product/ecosystem-statistics/upi |
| Tab | P2P and P2M Transactions |
| Granularity | One row per month |
| Coverage used | January 2022 to August 2026 (56 months) |

### 2.2 Fields

Each month provides six figures, stored in `data/upi_monthly.csv`:

| Column | Meaning | Unit |
|---|---|---|
| `month` | First day of the month | date |
| `volume_mn` | Total UPI transactions | million |
| `value_cr` | Total UPI value | ₹ crore |
| `p2p_volume_mn` | Person-to-person transactions | million |
| `p2p_value_cr` | Person-to-person value | ₹ crore |
| `p2m_volume_mn` | Person-to-merchant transactions | million |
| `p2m_value_cr` | Person-to-merchant value | ₹ crore |
| `days_in_month` | Derived | days |
| `avg_daily_volume_mn` | Derived: `volume_mn / days_in_month` | million/day |

Every month passes the consistency check `p2p_volume_mn + p2m_volume_mn = volume_mn`. Figures such as `29,82,355.95` on the website use the Indian digit-grouping format and were parsed by removing the commas.

### 2.3 Collection method

The NPCI site blocks plain HTTP requests (HTTP 403) and renders its data with JavaScript, showing one month at a time via Bootstrap dropdown menus. Collection was therefore automated with **Playwright**, which drives a real Chrome browser (`scrape_npci.py`):

1. Open the page and select the *P2P and P2M Transactions* tab.
2. For each year and month, choose the values in the two dropdowns.
3. Read the single table row shown on the page and parse its month label. Two label formats occur: `Aug-26` for recent months and `Jan'22` for older ones.
4. Save to CSV after every month so an interrupted run can resume.

Jan 2022 to May 2026 were collected automatically. June, July and August 2026 were not picked up by the scraper and were entered manually from the website. All three pass the P2P + P2M = Total check.

### 2.4 Scope decision: 2022 onwards

Data before 2022 is only available in NPCI's "archived" view, which could not be collected reliably. The analysis therefore starts in January 2022. This is acceptable and arguably preferable:

- 56 months gives about 1,700 daily points after generation, with more than two full years for yearly seasonality and rolling-origin cross-validation.
- Each recurring festival appears four to five times.
- The period starts after the COVID-19 lockdowns, so no pandemic structural break needs to be modelled.

### 2.5 Holiday calendar

Event dates for 2022 to 2026 are defined in `generate_synthetic.py` and exported to `data/prophet_holidays.csv`. Lunar festival dates (Diwali, Holi, Eid al-Fitr, Raksha Bandhan) should be verified against a panchang or official gazette before final submission, as an error of one day blurs the estimated effect.

---

## 3. Synthetic daily data generation

Script: `generate_synthetic.py`. Random seed: **1**.

### 3.1 Model

Each day's volume is a product of components:

$$
y_t = \text{trend}_t \times \text{weekday}_t \times \text{monthday}_t \times \text{holiday}_t \times \text{sale}_t \times \text{noise}_t \times f_{m(t)}
$$

where $f_{m(t)}$ is a monthly scaling factor (Section 3.4). The structure is multiplicative because UPI volume grew roughly fivefold over the period, so calendar effects scale with the level rather than adding a fixed count. This matches the `seasonality_mode='multiplicative'` setting used in Prophet.

### 3.2 Components

**Trend.** The average daily volume of each month is placed at mid-month and interpolated on the log scale with a monotone cubic (PCHIP) curve. This gives smooth growth without artificial steps at month boundaries.

**Weekday.** Assumed multipliers:

| | Mon to Fri | Sat | Sun |
|---|---|---|---|
| Total | 1.00 | 1.03 | 0.93 |
| P2M | 1.00 | 1.04 | 0.97 |

**Day of month (salary cycle).** Days 1 to 5 are multiplied by 1.06 (payday) and the last two days of each month by 1.03 (month-end). These match the `is_payday` and `is_monthend` regressor columns exactly.

**Known festivals and holidays.** Effects for the total series:

| Event | Window (days) | Effect |
|---|---|---|
| Diwali | −10 to +3 | Linear ramp to +20% on Dhanteras and Diwali eve (−2, −1), +15% on the day, −5% and −2% on the two days after |
| Holi | −2 to +1 | +8% on the two days before, −10% on the day |
| Eid al-Fitr | −3 to 0 | Ramps to +6% |
| Raksha Bandhan | −3 to 0 | Ramps to +5% |
| Republic Day, Independence Day, Gandhi Jayanti | 0 | −5% |

For P2M, positive festival effects are scaled by 1.5, and the Diwali peak is +30%, because merchant payments respond more strongly to festive shopping.

**Hidden sale event.** Each year, an e-commerce-style sale is placed about one week before the Diwali festive ramp begins. It starts 17 days before Diwali with a random shift of ±2 days, lasts a random 4 to 6 days, and lifts volume by a random 8% to 12% (×1.5 for P2M). This event is deliberately **not** included in the holiday list given to Prophet. It tests how the model behaves when a real effect is unknown to it: the unexplained lift should appear in the residuals and may distort the estimated Diwali ramp.

**Noise.** Log-normal noise with standard deviation 0.025 and first-order autocorrelation (AR(1), φ = 0.3). Autocorrelated noise is realistic and gives a meaningful residual diagnostic, because Prophet assumes independent errors.

### 3.3 Where effects overlap

Effects are multiplied. If a payday falls within the Diwali window, both apply.

### 3.4 Anchoring to real monthly totals

After all components are applied, each month is multiplied by a single factor

$$
f_m = \frac{\text{NPCI total}_m}{\sum_{t \in m} \text{generated}_t}
$$

so that the synthetic daily values sum exactly to the real NPCI monthly total. The script verifies that the maximum difference is zero.

### 3.5 Ground truth and the counterfactual

A second, **no-event counterfactual** series (`y_nohol`) is built from the same trend, weekday, day-of-month and noise components, but without holiday or sale effects. It uses the **same** monthly factor $f_m$ as the main series.

Using one shared factor is important. If the two series were scaled separately, the counterfactual October would be scaled up to the full NPCI total, and ordinary October days would appear to fall when Diwali is added, as if the festival were taking volume from the rest of the month. With a shared factor, the daily ratio

$$
\text{true\_effect}_t = \frac{y_t}{y^{\text{nohol}}_t} - 1
$$

equals exactly the injected holiday and sale multiplier on every day. For example, Dhanteras is exactly +20%. This is the value Prophet's estimated holiday effects are compared against.

A consequence of the shared factor is that, in festive months, the underlying level of ordinary days is slightly lower than in neighbouring months. The real NPCI total already includes the festival, so the month without it would have been smaller. Prophet will partly absorb this into its yearly seasonality.

---

## 4. Output files

| File | Contents |
|---|---|
| `data/upi_monthly.csv` | Real NPCI monthly data, Jan 2022 to Aug 2026 |
| `data/synthetic_daily.csv` | Daily synthetic series (see below) |
| `data/prophet_holidays.csv` | Known events in Prophet format (`holiday`, `ds`, `lower_window`, `upper_window`); the hidden sale is excluded |
| `data/truth.json` | Every true parameter: seed, multipliers, event dates and shapes, hidden sale dates and lifts, realised mean effect per event and year |
| `data/synthetic_check.png` | Full series plot and a zoom on the Diwali 2024 window |

Columns of `synthetic_daily.csv`:

| Column | Meaning |
|---|---|
| `ds` | Date |
| `y` | Synthetic total daily volume (million) |
| `y_nohol` | Counterfactual without holiday and sale effects |
| `true_effect` | Realised calendar-event effect on that day |
| `y_p2m`, `y_p2m_nohol`, `true_effect_p2m` | Same for the P2M series |
| `is_payday` | 1 on days 1 to 5 of the month |
| `is_monthend` | 1 on the last two days of the month |

---

## 5. Reproducibility

```bash
cd ~/upi_project
python scrape_npci.py --start 2022 --end 2026   # real monthly data
python generate_synthetic.py                    # synthetic daily data, seed 1
```

The same seed and the same `upi_monthly.csv` always produce the same synthetic series and the same hidden sale dates.

---

## 6. Limitations

- **Effect sizes are assumptions**, chosen to be plausible, not estimated from real daily data. Results show whether Prophet can recover effects of this kind and size, not what the true Diwali effect on UPI is.
- **The weekday pattern and payday effect are the same in every year**, whereas real behaviour may drift over time.
- **Only four Diwalis fall within the data** (2022 to 2025). Diwali 2026 is on 8 November, after the data ends.
- **Pre-2022 data was excluded** because the archived NPCI view could not be collected, so the COVID period and earlier structural changes are not covered.
- **Lunar festival dates must be verified** against an authoritative calendar.
