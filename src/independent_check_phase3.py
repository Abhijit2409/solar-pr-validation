"""Independent recomputation of selected development days, straight from the raw CSVs.

Deliberately shares no code with the production pipeline (ingest, timestamps,
pair_intervals, metrics). It re-parses raw text, converts local time to UTC with
plain pandas, finds the window with pvlib's solar position directly, pairs by
dictionary lookups in a loop, and sums energy and irradiation by hand.
Compares with results/phase3/daily_pr_dev.csv.

Usage:
    python src/independent_check_phase3.py
"""
from pathlib import Path

import pandas as pd
import pvlib

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/pvdaq/2023-solar-data-prize/2107_OEDI/data"
LAT, LON, ALT = 38.996306, -122.134111, 10.0   # site metadata
TZ = "America/Los_Angeles"
CAPACITY_KW = 893.0
MIN_ELEV = 5.0                                  # config/metric_definition.yaml reporting_window
AC_BOUNDS, POA_BOUNDS = (-44.65, 893.0), (-20.0, 2000.0)


def raw_series(fname, column, day):
    df = pd.read_csv(RAW / fname, dtype=str, keep_default_na=False)
    naive = pd.to_datetime(df["measured_on"], format="%Y-%m-%d %H:%M:%S")
    lo, hi = pd.Timestamp(day), pd.Timestamp(day) + pd.Timedelta(days=1)
    df = df[(naive >= lo) & (naive < hi)]
    naive = naive[(naive >= lo) & (naive < hi)]
    local = naive.dt.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")   # ambiguous rows dropped, not guessed
    utc = local.dt.tz_convert("UTC")
    vals = pd.to_numeric(df[column].replace("", None), errors="coerce")
    out = {}
    for t, v in zip(utc, vals):
        if pd.isna(t):
            continue
        assert t not in out, f"duplicate timestamp {t}"
        out[t] = v
    return out


def check_day(day):
    meter = raw_series("2107_meter_15m_data.csv", "meter_revenue_grade_ac_output_meter_149578", day)
    poa = raw_series("2107_irradiance_data.csv", "poa_irradiance_o_149574", day)
    start = pd.Timestamp(day).tz_localize(TZ).tz_convert("UTC")
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize(TZ).tz_convert("UTC")
    grid = pd.date_range(start, end, freq="15min", inclusive="left")
    elev = pvlib.solarposition.get_solarposition(grid + pd.Timedelta("7min30s"), LAT, LON, altitude=ALT)
    window = [t for t, e in zip(grid, elev["apparent_elevation"]) if e > MIN_ELEV]
    e_kwh = h_kwh_m2 = 0.0
    paired = 0
    for t in window:
        m = meter.get(t)
        if m is None or pd.isna(m) or not (AC_BOUNDS[0] <= m <= AC_BOUNDS[1]):
            continue
        subs = [poa.get(t + pd.Timedelta(minutes=k)) for k in (0, 5, 10)]
        if any(s is None or pd.isna(s) or not (POA_BOUNDS[0] <= s <= POA_BOUNDS[1]) for s in subs):
            continue
        paired += 1
        e_kwh += m * 0.25
        h_kwh_m2 += sum(s * (5 / 60) for s in subs) / 1000.0
    pr = (e_kwh / CAPACITY_KW) / h_kwh_m2 if h_kwh_m2 > 0 else float("nan")
    return {"local_reporting_date": day, "day_hours": (end - start).total_seconds() / 3600,
            "window_intervals": len(window), "paired_intervals": paired,
            "e_ac_kwh": e_kwh, "h_poa_kwh_m2": h_kwh_m2, "pr": pr}


def select_days(daily):
    """Deterministic selection by attribute from the development-period daily table."""
    d = daily.copy()
    d["month"] = d["local_reporting_date"].str.slice(5, 7)
    passed = d[d["status"] == "CHECKS_PASSED"]
    pick = {
        "clear summer (max H, Jun-Jul, passed)": passed[passed["month"].isin(["06", "07"])]
        .sort_values("h_poa_kwh_m2").iloc[-1],
        "clear winter (max H, Dec, passed)": passed[passed["month"] == "12"].sort_values("h_poa_kwh_m2").iloc[-1],
        "cloudy (min H, passed)": passed.sort_values("h_poa_kwh_m2").iloc[0],
        "most POA_MISSING exclusions with pairs": d[d["paired_intervals"] > 0]
        .sort_values(["n_excl_poa_missing", "local_reporting_date"]).iloc[-1],
        "catch-up spike day": d[d["local_reporting_date"] == "2018-02-18"].iloc[0],
        "most zero-output pairs (review)": d.sort_values(["n_paired_zero_output", "local_reporting_date"]).iloc[-1],
        "25-hour DST day": d[d["local_day_length_h"] == 25].iloc[0],
        "23-hour DST day": d[d["local_day_length_h"] == 23].iloc[0],
    }
    return {k: v["local_reporting_date"] for k, v in pick.items()}


def main():
    daily = pd.read_csv(ROOT / "results/phase3/daily_pr_dev.csv")
    rows = []
    for label, day in select_days(daily).items():
        ind = check_day(day)
        prod = daily[daily["local_reporting_date"] == day].iloc[0]
        row = {"case": label, **ind, "prod_status": prod["status"],
               "prod_window_intervals": prod["window_intervals"], "prod_paired_intervals": prod["paired_intervals"],
               "prod_e_ac_kwh": prod["e_ac_kwh"], "prod_h_poa_kwh_m2": prod["h_poa_kwh_m2"],
               "prod_diagnostic_pr": prod["diagnostic_pr"]}
        row["match"] = bool(ind["window_intervals"] == prod["window_intervals"]
                            and ind["paired_intervals"] == prod["paired_intervals"]
                            and abs(ind["e_ac_kwh"] - prod["e_ac_kwh"]) <= 1e-9 * max(1, abs(ind["e_ac_kwh"]))
                            and abs(ind["h_poa_kwh_m2"] - prod["h_poa_kwh_m2"]) <= 1e-12 * max(1, ind["h_poa_kwh_m2"])
                            and abs(ind["pr"] - prod["diagnostic_pr"]) <= 1e-12)
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "results/phase3/independent_check_dev.csv", index=False)
    print(out[["case", "local_reporting_date", "window_intervals", "paired_intervals", "e_ac_kwh",
               "h_poa_kwh_m2", "pr", "prod_status", "match"]].to_string(index=False))
    if not out["match"].all():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
