#!/usr/bin/env python3
"""
generate_synthetic.py - SYNTHETIC daily UPI volume, anchored to real NPCI
monthly totals, with KNOWN (ground-truth) calendar effects.

  y_t = trend_t * weekday_t * monthday_t * holiday_t * sale_t * noise_t
  then every month is rescaled to sum exactly to the NPCI total.

Inputs : data/upi_monthly.csv
Outputs: data/synthetic_daily.csv    daily series (+ no-holiday counterfactual)
         data/prophet_holidays.csv   KNOWN events only (sale is hidden)
         data/truth.json             every true parameter + realised effects
         data/synthetic_check.png    visual sanity check

Run: python generate_synthetic.py
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

SEED = 1
rng = np.random.default_rng(SEED)
DATA = Path("data")

# ------------------------------------------------------------- the rules
WEEKDAY = {  # Mon=0 ... Sun=6
    "total": [1.00, 1.00, 1.00, 1.00, 1.00, 1.03, 0.93],
    "p2m":   [1.00, 1.00, 1.00, 1.00, 1.00, 1.04, 0.97],
}
PAYDAY_DAYS, PAYDAY_LIFT = range(1, 6), 0.06
MONTHEND_DAYS, MONTHEND_LIFT = 2, 0.03
NOISE_SIGMA, NOISE_PHI = 0.025, 0.3
FESTIVE_LIFT_THRESHOLD = 0.01     # months whose injected event lift exceeds 1% are not anchored
P2M_FESTIVAL_SCALE = 1.5          # merchant payments react more to festivals

DATES = {  # verify lunar dates against a panchang before submission
    "diwali":        ["2022-10-24", "2023-11-12", "2024-10-31", "2025-10-20", "2026-11-08"],
    "holi":          ["2022-03-18", "2023-03-08", "2024-03-25", "2025-03-14", "2026-03-04"],
    "eid_al_fitr":   ["2022-05-03", "2023-04-22", "2024-04-11", "2025-03-31", "2026-03-20"],
    "raksha_bandhan": ["2022-08-11", "2023-08-30", "2024-08-19", "2025-08-09", "2026-08-28"],
    "republic_day":  [f"{y}-01-26" for y in range(2022, 2027)],
    "independence_day": [f"{y}-08-15" for y in range(2022, 2027)],
    "gandhi_jayanti": [f"{y}-10-02" for y in range(2022, 2027)],
}


def diwali_shape(peak):
    """Offset -> lift. Ramp from -10 to peak on Dhanteras/eve, dip after."""
    s = {k: peak * (k + 11) / 9 for k in range(-10, -2)}
    s.update({-2: peak, -1: peak, 0: 0.75 * peak, 1: -0.05, 2: -0.02, 3: 0.0})
    return s


def shapes(scale):
    return {
        "diwali": diwali_shape(0.20 if scale == 1 else 0.30),
        "holi": {-2: 0.08 * scale, -1: 0.08 * scale, 0: -0.10, 1: 0.0},
        "eid_al_fitr": {-3: 0.02 * scale, -2: 0.04 * scale, -1: 0.06 * scale, 0: 0.06 * scale},
        "raksha_bandhan": {-3: 0.02 * scale, -2: 0.035 * scale, -1: 0.05 * scale, 0: 0.05 * scale},
        "republic_day": {0: -0.05},
        "independence_day": {0: -0.05},
        "gandhi_jayanti": {0: -0.05},
    }


# Hidden sale: starts ~1 week before the Diwali ramp begins (Diwali-10),
# random start jitter, length and lift. NOT given to Prophet.
def make_sales():
    sales = []
    for d in DATES["diwali"]:
        d = pd.Timestamp(d)
        start = d - pd.Timedelta(days=17 + int(rng.integers(-2, 3)))
        length = int(rng.integers(4, 7))           # 4-6 days
        lift = float(round(rng.uniform(0.08, 0.12), 4))
        sales.append({"start": str(start.date()), "days": length, "lift": lift})
    return sales


# ------------------------------------------------------------- building
def trend(days, monthly, col, use=None):
    """Smooth daily trend through monthly average daily volume (log-PCHIP).
    use: boolean mask of months to use as knots (default: all months)."""
    use = np.ones(len(monthly), bool) if use is None else np.asarray(use)
    monthly = monthly[use]
    mid = monthly.month + pd.to_timedelta(monthly.days_in_month / 2 - 0.5, unit="D")
    x = mid.values.astype("datetime64[D]").astype(float)
    logv = np.log(monthly[col] / monthly.days_in_month)
    t = days.values.astype("datetime64[D]").astype(float)
    try:
        from scipy.interpolate import PchipInterpolator
        f = PchipInterpolator(x, logv, extrapolate=True)
        return np.exp(f(t))
    except ImportError:
        return np.exp(np.interp(t, x, logv))


def ar1_noise(n):
    e = np.zeros(n)
    z = rng.standard_normal(n) * NOISE_SIGMA * np.sqrt(1 - NOISE_PHI ** 2)
    for i in range(n):
        e[i] = (NOISE_PHI * e[i - 1] if i else 0) + z[i]
    return np.exp(e)


def holiday_mult(days, shp):
    mult = pd.Series(1.0, index=days)
    for name, dates in DATES.items():
        for d in dates:
            for k, lift in shp[name].items():
                day = pd.Timestamp(d) + pd.Timedelta(days=k)
                if day in mult.index:
                    mult[day] *= 1 + lift
    return mult.values


def sale_mult(days, sales, lift_scale):
    mult = pd.Series(1.0, index=days)
    for s in sales:
        for k in range(s["days"]):
            day = pd.Timestamp(s["start"]) + pd.Timedelta(days=k)
            if day in mult.index:
                mult[day] *= 1 + s["lift"] * lift_scale
    return mult.values


def scale_factor(values, days, monthly, col, anchored):
    """Per-day factor that makes each ANCHORED month sum to its NPCI total.
    Festive (non-anchored) months get the geometric mean of the factors of the
    nearest anchored months before and after, so they follow the smooth trend
    and the festival sits on top of it."""
    s = pd.Series(values, index=days)
    month_start = s.index.to_period("M").to_timestamp()
    f_m = monthly[col].values / s.groupby(month_start).sum().values
    log_f = pd.Series(np.where(anchored, np.log(f_m), np.nan))
    log_f = log_f.interpolate(limit_direction="both")      # fill festive months
    return np.exp(month_start.map(dict(zip(monthly.month, log_f.values))).values)


def main():
    monthly = pd.read_csv(DATA / "upi_monthly.csv", parse_dates=["month"])
    days = pd.date_range(monthly.month.min(),
                         monthly.month.max() + pd.offsets.MonthEnd(0), freq="D")
    n = len(days)
    sales = make_sales()

    dom = days.day.values
    is_payday = np.isin(dom, list(PAYDAY_DAYS)).astype(int)
    is_monthend = (days.days_in_month.values - dom < MONTHEND_DAYS).astype(int)
    monthday = (1 + PAYDAY_LIFT * is_payday) * (1 + MONTHEND_LIFT * is_monthend)

    out = pd.DataFrame({"ds": days, "is_payday": is_payday, "is_monthend": is_monthend})
    month_start = days.to_period("M").to_timestamp()

    # Festive months: injected event lift (holidays + sale) above the threshold.
    # Their real NPCI totals do not reliably contain a bump of that size, so forcing
    # them to match NPCI would push their ordinary days below trend (v1 bug).
    hol_total = holiday_mult(days, shapes(1)) * sale_mult(days, sales, 1)
    lift = pd.Series(hol_total, index=days).groupby(month_start).mean().values - 1
    anchored = lift <= FESTIVE_LIFT_THRESHOLD
    festive_months = [str(m.date())[:7] for m in monthly.month[~anchored]]
    print("festive (not anchored) months:", festive_months)

    for series, col, scale in [("total", "volume_mn", 1), ("p2m", "p2m_volume_mn", P2M_FESTIVAL_SCALE)]:
        base = trend(days, monthly, col, use=anchored) * np.array(WEEKDAY[series])[days.dayofweek] \
            * monthday * ar1_noise(n)
        hol = holiday_mult(days, shapes(scale)) * sale_mult(days, sales, scale)
        f = scale_factor(base * hol, days, monthly, col, anchored)
        y = base * hol * f          # matches NPCI monthly totals exactly
        y0 = base * f               # same scaling -> y/y0 == injected effect
        suffix = "" if series == "total" else "_p2m"
        out[f"y{suffix}"] = y
        out[f"y{suffix}_nohol"] = y0
        out[f"true_effect{suffix}"] = y / y0 - 1          # realised effect per day

    out.to_csv(DATA / "synthetic_daily.csv", index=False)

    # Prophet holiday frame: known events only (sale deliberately excluded)
    rows = []
    for name, dates in DATES.items():
        ks = shapes(1)[name].keys()
        for d in dates:
            rows.append((name, d, min(ks), max(ks)))
    pd.DataFrame(rows, columns=["holiday", "ds", "lower_window", "upper_window"]) \
        .to_csv(DATA / "prophet_holidays.csv", index=False)

    # how far festive-month totals end up from NPCI (anchored months are exact)
    mt = out.groupby(month_start)[["y", "y_p2m"]].sum()
    festive_dev = {str(m.date())[:7]: {
        "total": round(float(100 * (mt.loc[m, "y"] / v - 1)), 2),
        "p2m": round(float(100 * (mt.loc[m, "y_p2m"] / p - 1)), 2)}
        for m, v, p, a in zip(monthly.month, monthly.volume_mn, monthly.p2m_volume_mn, anchored) if not a}

    # realised effect by event-year (after rescaling), for later comparison
    realised = {}
    for name, dates in DATES.items():
        for d in dates:
            ks = shapes(1)[name].keys()
            win = pd.date_range(pd.Timestamp(d) + pd.Timedelta(days=min(ks)),
                                pd.Timestamp(d) + pd.Timedelta(days=max(ks)))
            sel = out.ds.isin(win)
            if sel.any():
                realised[f"{name}_{d[:4]}"] = {
                    "total_mean_lift": round(float(out.loc[sel, "true_effect"].mean()), 4),
                    "p2m_mean_lift": round(float(out.loc[sel, "true_effect_p2m"].mean()), 4)}

    truth = {
        "WARNING": "SYNTHETIC DATA - effects are assumptions, not measurements",
        "seed": SEED,
        "weekday_multipliers_mon_to_sun": WEEKDAY,
        "payday": {"days": list(PAYDAY_DAYS), "lift": PAYDAY_LIFT},
        "monthend": {"last_n_days": MONTHEND_DAYS, "lift": MONTHEND_LIFT},
        "noise": {"sigma": NOISE_SIGMA, "ar1_phi": NOISE_PHI},
        "p2m_festival_scale": P2M_FESTIVAL_SCALE,
        "event_dates": DATES,
        "event_shapes_total": {k: {str(o): round(v, 4) for o, v in s.items()}
                               for k, s in shapes(1).items()},
        "hidden_sale_NOT_given_to_prophet": sales,
        "realised_effects_after_rescaling": realised,
        "generator_version": 2,
        "festive_months_not_anchored": festive_months,
        "festive_month_total_vs_npci_pct": festive_dev,
    }
    (DATA / "truth.json").write_text(json.dumps(truth, indent=2))

    # checks
    chk = out.groupby(out.ds.dt.to_period("M").dt.to_timestamp())["y"].sum()
    err = (chk.values - monthly.set_index("month").loc[chk.index, "volume_mn"].values)
    print(f"{n} days, {days.min().date()} to {days.max().date()}")
    print(f"max |monthly sum - NPCI| over anchored months = {np.abs(err[anchored]).max():.6f} Mn")
    print("festive months, synthetic total vs NPCI (%):", festive_dev)
    print("hidden sales:", [(s['start'], s['days'], s['lift']) for s in sales])

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(13, 7))
        ax[0].plot(out.ds, out.y, lw=0.6)
        ax[0].set_title("SYNTHETIC daily UPI volume (Mn) - anchored to NPCI monthly totals")
        z = out[(out.ds >= "2024-09-15") & (out.ds <= "2024-11-15")]
        ax[1].plot(z.ds, z.y, label="with events")
        ax[1].plot(z.ds, z.y_nohol, label="no-event counterfactual", alpha=0.7)
        ax[1].axvline(pd.Timestamp("2024-10-31"), ls="--", c="k", lw=0.8)
        ax[1].set_title("Zoom: Diwali 2024 window (dashed) and hidden sale before it")
        ax[1].legend()
        fig.tight_layout()
        fig.savefig(DATA / "synthetic_check.png", dpi=120)
        print("plot -> data/synthetic_check.png")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
