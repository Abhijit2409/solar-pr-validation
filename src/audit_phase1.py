"""Phase 1 read-only audit of PVDAQ system 2107 meter and POA irradiance files.

Profiles each raw CSV without converting, repairing or interpolating it.
Writes summaries to results/phase1/. No Performance Ratio is calculated here.

Usage:
    python src/audit_phase1.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "raw" / "pvdaq" / "2023-solar-data-prize" / "2107_OEDI" / "data"
OUT = ROOT / "results" / "phase1"

LAT, LON = 38.996306, -122.134111      # from 2107_system_metadata.json
DC_KW = 893.0                           # metadata power(kW DC)
INV_AC_KW_NAMEPLATE = 24 * 27.6         # 24 x ABB TRIO-27.6 (model-name value, datasheet not checked)
SENTINELS = [-9999, -999, -99, 9999, 99999, -1]

FILES = {
    "meter_2017_2023": ("2107_meter_15m_data.csv", "meter_revenue_grade_ac_output_meter_149578"),
    "meter_2024": ("2107_meter_15m_data_2024.csv", "meter_revenue_grade_ac_output_meter_149578"),
    "meter_2025": ("2107_meter_15m_data_2025.csv", "meter_revenue_grade_ac_output_meter_149578"),
    "poa_2017_2023": ("2107_irradiance_data.csv", "poa_irradiance_o_149574"),
    "poa_2024": ("2107_irradiance_data_2024.csv", "poa_irradiance_o_149574"),
    "poa_2025": ("2107_irradiance_15m_data_2025.csv", "poa_irradiance_o_149574"),
}


def solar_noon_standard_hours(day_of_year):
    """Approximate solar noon in local standard time (UTC-8), NOAA equation of time."""
    g = 2 * np.pi / 365 * (day_of_year - 1)
    eot = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                    - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    return 12 + (-8 * 15 - LON) * 4 / 60 - eot / 60


def load(name):
    fname, col = FILES[name]
    raw = pd.read_csv(DATA / fname, dtype=str, keep_default_na=False)
    ts_col = raw.columns[0]
    ts = pd.to_datetime(raw[ts_col], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    val = pd.to_numeric(raw[col].replace("", np.nan), errors="coerce")
    df = pd.DataFrame({"ts": ts, "value": val, "raw_value": raw[col]})
    return df, ts_col, list(raw.columns)


def runs_of_constant(values, min_len):
    """Count runs of identical non-zero, non-null consecutive values of at least min_len."""
    v = values.to_numpy()
    runs, i = [], 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[j + 1] == v[i]:
            j += 1
        if (j - i + 1) >= min_len and not np.isnan(v[i]) and v[i] != 0:
            runs.append((i, j - i + 1, v[i]))
        i = j + 1
    return runs


def profile(name):
    df, ts_col, cols = load(name)
    out = {"dataset": name, "file": FILES[name][0], "columns": cols, "timestamp_column": ts_col,
           "rows": len(df), "unparseable_timestamps": int(df.ts.isna().sum()),
           "first_ts": str(df.ts.min()), "last_ts": str(df.ts.max())}
    ordered = df.ts.is_monotonic_increasing
    out["timestamps_monotonic"] = bool(ordered)
    out["duplicate_timestamps"] = int(df.ts.duplicated(keep=False).sum())
    dup_conflict = df[df.ts.duplicated(keep=False)].groupby("ts").value.nunique()
    out["duplicate_timestamps_with_conflicting_values"] = int((dup_conflict > 1).sum())
    d = df.ts.sort_values().diff().dropna()
    vc = d.value_counts()
    out["interval_counts_top"] = {str(k): int(v) for k, v in vc.head(8).items()}
    nominal = vc.index[0]
    out["nominal_interval"] = str(nominal)
    grid = pd.date_range(df.ts.min().ceil(nominal), df.ts.max().floor(nominal), freq=nominal)
    uniq = pd.Index(df.ts.dropna().unique())
    out["expected_grid_rows"] = len(grid)
    out["grid_rows_missing"] = int(len(grid.difference(uniq)))
    out["off_grid_timestamps"] = int(len(uniq.difference(grid)))
    gaps = d[d > nominal]
    out["gaps_gt_nominal"] = int(len(gaps))
    out["gaps_gt_1day"] = int((gaps > pd.Timedelta("1D")).sum())
    big = df.ts.sort_values().reset_index(drop=True)
    idx = d[d > pd.Timedelta("1D")].index
    out["largest_gaps"] = sorted(
        [{"after": str(big.iloc[i - 1]), "before": str(big.iloc[i]), "length": str(d.loc[i])}
         for i in idx], key=lambda r: pd.Timedelta(r["length"]), reverse=True)[:10]
    v = df.value
    out["blank_or_nonnumeric_values"] = int(v.isna().sum())
    out["nonnumeric_examples"] = sorted(set(df.raw_value[v.isna() & (df.raw_value != "")]))[:10]
    out["negative_values"] = int((v < 0).sum())
    out["min"], out["max"] = float(v.min()), float(v.max())
    out["percentiles"] = {str(q): float(v.quantile(q)) for q in (0.5, 0.9, 0.99, 0.999)}
    out["zero_values"] = int((v == 0).sum())
    out["sentinel_counts"] = {str(s): int((v == s).sum()) for s in SENTINELS if (v == s).sum()}
    out["top_repeated_values"] = {str(k): int(c) for k, c in v.value_counts().head(5).items()}
    if name.startswith("poa"):
        out["values_gt_1500_wm2"] = int((v > 1500).sum())
        out["values_gt_1300_wm2"] = int((v > 1300).sum())
    else:
        out["values_gt_dc_capacity_kw"] = int((v > DC_KW).sum())
        out["values_gt_inverter_ac_nameplate_kw"] = int((v > INV_AC_KW_NAMEPLATE).sum())
        out["max_as_fraction_of_dc"] = round(float(v.max()) / DC_KW, 3)
    s = df.sort_values("ts").reset_index(drop=True)
    min_len = int(pd.Timedelta("2h") / nominal)
    runs = runs_of_constant(s.value, min_len)
    out["nonzero_flat_runs_ge_2h"] = len(runs)
    out["nonzero_flat_run_examples"] = [
        {"start": str(s.ts[i]), "intervals": n, "value": float(val)} for i, n, val in
        sorted(runs, key=lambda r: -r[1])[:5]]
    return out, df


def dst_window_check(df, name):
    """List timestamps around US DST transitions (local clock) to see whether
    the clock skips 02:00 in March and repeats 01:00 in November."""
    rows = []
    for y in range(2017, 2026):
        mar = pd.Timestamp(f"{y}-03-07") + pd.offsets.Week(weekday=6)   # 2nd Sunday
        nov = pd.Timestamp(f"{y}-10-31") + pd.offsets.Week(weekday=6)
        for label, day in (("spring", mar), ("fall", nov)):
            w = df[(df.ts >= day + pd.Timedelta("0h30min")) & (df.ts < day + pd.Timedelta("3h30min"))]
            if w.empty:
                continue
            rows.append({"dataset": name, "transition": label, "date": str(day.date()),
                         "rows_0030_0330": len(w),
                         "rows_in_0200_0259": int(((w.ts.dt.hour == 2)).sum()),
                         "duplicates_in_window": int(w.ts.duplicated().sum())})
    return rows


def noon_timing(meter, poa):
    """Monthly irradiance- and power-weighted midpoint of the day (clock hours)
    versus calculated solar noon in standard time (UTC-8)."""
    rows = []
    for label, df, shift in (("poa", poa, 0), ("meter", meter, 0)):
        x = df.dropna(subset=["ts", "value"])
        x = x[x.value > 0]
        # meter timestamps are interval-start labels: use interval midpoint
        mid = x.ts + (pd.Timedelta("7min30s") if label == "meter" else pd.Timedelta("2min30s"))
        hours = mid.dt.hour + mid.dt.minute / 60 + mid.dt.second / 3600
        g = pd.DataFrame({"month": x.ts.dt.to_period("M"), "h": hours, "w": x.value,
                          "doy": x.ts.dt.dayofyear})
        for m, grp in g.groupby("month"):
            if len(grp) < 200:
                continue
            centroid = np.average(grp.h, weights=grp.w)
            noon = float(np.mean(solar_noon_standard_hours(grp.doy.to_numpy())))
            rows.append({"signal": label, "month": str(m), "weighted_midpoint_h": round(centroid, 3),
                         "solar_noon_std_h": round(noon, 3),
                         "offset_min": round((centroid - noon) * 60, 1)})
    return pd.DataFrame(rows)


def overlap_check(a, b, label):
    """Compare overlapping files. Tests offsets of 0, 7 and 8 hours applied to b."""
    res = {"pair": label}
    for h in (0, -7, -8, 7, 8):
        bb = b.assign(ts=b.ts + pd.Timedelta(hours=h))
        m = a.merge(bb, on="ts", suffixes=("_a", "_b")).dropna(subset=["value_a", "value_b"])
        m = m[(m.value_a > 0) | (m.value_b > 0)]
        if len(m) == 0:
            res[f"shift_{h}h"] = {"matched_nonzero_rows": 0}
            continue
        res[f"shift_{h}h"] = {"matched_nonzero_rows": len(m),
                              "exact_equal_share": round(float((np.isclose(m.value_a, m.value_b)).mean()), 4),
                              "corr": round(float(m.value_a.corr(m.value_b)), 4)}
    return res


def supplementary(frames):
    """Targeted follow-up checks on issues raised by the first profiling pass."""
    meter, poa = frames["meter_2017_2023"], frames["poa_2017_2023"]
    out = {}

    # POA ceiling: how often is the value exactly 1400 W/m2, and in which hours?
    cap = poa[poa.value == 1400.0]
    out["poa_exact_1400_rows_main_file"] = len(cap)
    out["poa_exact_1400_by_hour"] = {int(k): int(v) for k, v in cap.ts.dt.hour.value_counts().sort_index().items()}
    out["poa_exact_1400_examples"] = [str(t) for t in cap.ts.head(5)]

    # POA 5-min grid missingness by local clock hour (main file)
    grid = pd.date_range("2018-01-01", "2023-10-31 23:55", freq="5min")
    present = pd.Index(poa.ts).intersection(grid)
    miss = pd.Series(1, index=grid.difference(present))
    tot = pd.Series(1, index=grid).groupby(grid.hour).size()
    out["poa_missing_share_by_hour_2018_2023"] = {
        int(h): round(float(miss.groupby(miss.index.hour).size().get(h, 0) / tot[h]), 3) for h in tot.index}
    mgrid = pd.date_range("2018-01-01", "2023-10-31 23:45", freq="15min")
    mmiss = mgrid.difference(pd.Index(meter.ts))
    out["meter_missing_15min_rows_2018_2023"] = len(mmiss)

    # POA 2025 file: when does sampling change between 5 and 15 minutes?
    p25 = frames["poa_2025"].sort_values("ts")
    step = p25.ts.diff().dt.total_seconds().div(60)
    monthly_mode = step.groupby(p25.ts.dt.to_period("M")).agg(lambda s: s.mode().iloc[0])
    out["poa_2025_file_modal_step_min_by_month"] = {str(k): float(v) for k, v in monthly_mode.items()}

    # POA 2025 file noon timing after converting UTC to fixed UTC-8 (standard time)
    p25s = p25.assign(ts=p25.ts - pd.Timedelta(hours=8))
    m25 = frames["meter_2025"]
    nt = noon_timing(m25, p25s)
    out["poa_2025_noon_offset_min_after_utc_to_utc-8"] = nt[nt.signal == "poa"].set_index("month").offset_min.to_dict()

    # Relative alignment: correlate meter (15-min, label as given) with POA
    # averaged into 15-min buckets whose labels are shifted by k minutes.
    # Daylight months in standard time only (Nov-Feb) to avoid DST effects.
    std = poa[poa.ts.dt.month.isin([11, 12, 1, 2]) & (poa.ts.dt.year.between(2018, 2023))]
    m = meter.set_index("ts").value
    lag = {}
    for k in range(-30, 35, 5):
        b = std.assign(bucket=(std.ts + pd.Timedelta(minutes=k)).dt.floor("15min")).groupby("bucket").value.mean()
        j = pd.concat([m.rename("m"), b.rename("p")], axis=1, join="inner")
        j = j[j.p > 20]
        lag[k] = round(float(j.m.corr(j.p)), 4)
    out["meter_vs_poa_corr_by_poa_label_shift_min_std_time_months"] = lag

    # Raw rows around one fall-back transition (repeated 01:00 hour)
    win = lambda df: df[(df.ts >= "2019-11-03 00:30") & (df.ts < "2019-11-03 03:00")]
    out["fall_back_2019_meter_rows"] = [(str(t), v) for t, v in zip(win(meter).ts, win(meter).value)]
    out["fall_back_2019_poa_rows"] = len(win(poa))
    return out


def genuine_gap_recount(frames):
    """Separate real missing rows from clock and sampling artefacts.

    Local-clock grids contain spring-forward times that never existed; these are
    removed by localising to America/Los_Angeles (same DST rules as PST8PDT).
    The repeated fall-back hour cannot be counted from a file that holds it once.
    The 2025 POA file (UTC) is split into its 5-min and 15-min sampling segments.
    """
    out = {}
    for name in ("meter_2017_2023", "meter_2024", "meter_2025", "poa_2017_2023", "poa_2024"):
        df = frames[name]
        step = "15min" if name.startswith("meter") else "5min"
        grid = pd.date_range(df.ts.min(), df.ts.max(), freq=step)
        loc = grid.tz_localize("America/Los_Angeles", nonexistent="NaT", ambiguous=True)
        real = grid[~loc.isna()]
        missing = real.difference(pd.Index(df.ts))
        daytime = missing[(missing.hour >= 8) & (missing.hour < 17)]
        real_day = real[(real.hour >= 8) & (real.hour < 17)]
        out[name] = {"grid_step": step, "nonexistent_spring_forward_slots_removed": int(loc.isna().sum()),
                     "genuine_missing_rows": len(missing),
                     "genuine_missing_rows_08_17_local": len(daytime),
                     "missing_share_08_17_local": round(len(daytime) / len(real_day), 4),
                     "missing_days_with_gt_1h_daytime_gap": int(
                         pd.Series(1, index=daytime).groupby(daytime.date).size()
                         .gt(pd.Timedelta("1h") / pd.Timedelta(step)).sum())}
    p25 = frames["poa_2025"]
    for label, lo, hi, step in (("poa_2025_5min_segment", "2024-02-01", "2024-11-01", "5min"),
                                ("poa_2025_15min_segment", "2024-11-01", "2026-01-01", "15min")):
        seg = p25[(p25.ts >= lo) & (p25.ts < hi)]
        grid = pd.date_range(seg.ts.min().ceil(step), seg.ts.max(), freq=step)
        missing = grid.difference(pd.Index(seg.ts))
        local = (missing - pd.Timedelta(hours=8))          # UTC -> standard time for hour bucketing
        grid_local = grid - pd.Timedelta(hours=8)
        day = (local.hour >= 8) & (local.hour < 17)
        out[label] = {"grid_step": step, "rows": len(seg), "genuine_missing_rows": len(missing),
                      "off_grid_rows": int(len(pd.Index(seg.ts).difference(grid))),
                      "missing_share_08_17_standard_time": round(
                          day.sum() / ((grid_local.hour >= 8) & (grid_local.hour < 17)).sum(), 4)}
    return out


def daylight_zero_events(frames, ceiling_kw=720.0):
    """Daylight meter zero runs (POA > 200 W/m2), split by whether the next
    non-zero meter value exceeds the observed plant ceiling (catch-up pattern)."""
    rows = []
    for mname, pname in (("meter_2017_2023", "poa_2017_2023"), ("meter_2024", "poa_2024")):
        m = frames[mname].set_index("ts").value.sort_index()
        p = frames[pname]
        p15 = p.assign(b=p.ts.dt.floor("15min")).groupby("b").value.mean()
        j = pd.concat([m.rename("m"), p15.rename("p")], axis=1).sort_index()
        j = j[j.m.notna()]
        zero_day = (j.m <= 0) & (j.p > 200)
        run_id = (zero_day != zero_day.shift()).cumsum()
        for _, grp in j[zero_day].groupby(run_id[zero_day]):
            after = j.loc[j.index > grp.index[-1], "m"]
            nxt = after[after > 0].head(1)
            rows.append({"file": mname, "start": grp.index[0], "intervals": len(grp),
                         "next_nonzero_kw": float(nxt.iloc[0]) if len(nxt) else np.nan,
                         "next_nonzero_ts": nxt.index[0] if len(nxt) else pd.NaT})
    ev = pd.DataFrame(rows)
    ev["catch_up_pattern"] = ev.next_nonzero_kw > ceiling_kw
    ev.to_csv(OUT / "daylight_zero_events.csv", index=False)
    return {"daylight_zero_runs": len(ev), "daylight_zero_intervals": int(ev.intervals.sum()),
            "runs_followed_by_value_gt_ceiling": int(ev.catch_up_pattern.sum()),
            "intervals_in_catch_up_runs": int(ev[ev.catch_up_pattern].intervals.sum()),
            "ceiling_kw_used": ceiling_kw,
            "catch_up_examples": ev[ev.catch_up_pattern].head(5).astype(str).to_dict("records")}


def boundary_check():
    """Compare the plant meter with the sum of the 24 inverter AC power channels
    (2024 files, both local-clock 'measured_on')."""
    el = pd.read_csv(DATA / "2107_electrical_data_2024.csv")
    el["ts"] = pd.to_datetime(el.measured_on)
    ac_cols = [c for c in el.columns if c.endswith("_ac_power_inv_" + c.split("_inv_")[-1]) and "ac_power" in c]
    out = {"inverter_ac_power_columns": len(ac_cols), "electrical_rows": len(el),
           "electrical_modal_step": str(el.ts.diff().mode().iloc[0]),
           "rows_with_any_null_ac_power": int(el[ac_cols].isna().any(axis=1).sum())}
    out["per_inverter_max_kw"] = {c.split("_ac_power")[0]: float(el[c].max()) for c in ac_cols}
    out["per_inverter_p999_kw"] = {c.split("_ac_power")[0]: round(float(el[c].quantile(0.999)), 2) for c in ac_cols}
    complete = el[el[ac_cols].notna().all(axis=1)].copy()
    complete["inv_sum"] = complete[ac_cols].sum(axis=1)
    out["inverter_sum_max_kw"] = float(complete.inv_sum.max())
    meter = pd.read_csv(DATA / "2107_meter_15m_data_2024.csv", parse_dates=["measured_on"])
    meter = meter.set_index("measured_on").iloc[:, 0].rename("meter")
    ratios = {}
    for k in range(-15, 20, 5):
        b = complete.assign(bk=(complete.ts + pd.Timedelta(minutes=k)).dt.floor("15min"))
        g = b.groupby("bk").inv_sum.agg(["mean", "count"])
        g = g[g["count"] == 3]
        jj = pd.concat([meter, g["mean"].rename("inv")], axis=1, join="inner")
        jj = jj[jj.meter > 100]
        ratios[k] = {"rows": len(jj), "corr": round(float(jj.meter.corr(jj.inv)), 4),
                     "median_meter_over_inverter_sum": round(float((jj.meter / jj.inv).median()), 4)}
    out["meter_vs_inverter_sum_by_inverter_label_shift_min"] = ratios

    # POA vs inverter sum at native 5-min resolution, standard-time months only
    poa = pd.read_csv(DATA / "2107_irradiance_data_2024.csv", parse_dates=["measured_on"])
    poa = poa.set_index("measured_on").iloc[:, 0].rename("poa")
    inv = complete.set_index("ts").inv_sum
    lag = {}
    for k in range(-20, 25, 5):
        shifted = poa.copy()
        shifted.index = shifted.index + pd.Timedelta(minutes=k)
        jj = pd.concat([inv, shifted], axis=1, join="inner")
        jj = jj[(jj.poa > 20) & jj.index.month.isin([1, 2, 11, 12])]
        lag[k] = {"rows": len(jj), "corr": round(float(jj.inv_sum.corr(jj.poa)), 4)}
    out["inverter_sum_vs_poa_corr_by_poa_label_shift_min_std_months_2024"] = lag
    return out


def poa_daytime_coverage(frames):
    """Share of 08:00-17:00 local-clock 5-min POA slots missing, per year, and
    per month for the first two years of the main file."""
    poa = frames["poa_2017_2023"]
    grid = pd.date_range("2017-11-01", "2023-11-01 23:55", freq="5min")
    grid = grid[(grid.hour >= 8) & (grid.hour < 17)]
    present = pd.Series(grid.isin(pd.Index(poa.ts)), index=grid)
    by_year = (1 - present.groupby(grid.year).mean()).round(4)
    early = present[grid < "2019-01-01"]
    by_month = (1 - early.groupby(early.index.to_period("M")).mean()).round(4)
    return {"missing_share_08_17_by_year": {int(k): float(v) for k, v in by_year.items()},
            "missing_share_08_17_by_month_2017_2018": {str(k): float(v) for k, v in by_month.items()}}


def boundary_check_primary_window():
    """Meter vs inverter-sum ratio and POA-vs-inverter lag inside 2017-2023,
    using the corrected-header electrical file (2107_electrical_data_v1.csv)."""
    path = DATA / "2107_electrical_data_v1.csv"
    header = pd.read_csv(path, nrows=0).columns
    ac_cols = [c for c in header if "_ac_power_" in c]
    parts = []
    for chunk in pd.read_csv(path, usecols=["measured_on"] + ac_cols, chunksize=200_000):
        chunk["ts"] = pd.to_datetime(chunk.measured_on)
        ok = chunk[ac_cols].notna().all(axis=1)
        parts.append(pd.DataFrame({"ts": chunk.ts[ok], "inv_sum": chunk.loc[ok, ac_cols].sum(axis=1)}))
    el = pd.concat(parts, ignore_index=True)
    out = {"inverter_ac_power_columns": len(ac_cols), "complete_rows": len(el),
           "first_ts": str(el.ts.min()), "last_ts": str(el.ts.max()),
           "modal_step": str(el.ts.diff().mode().iloc[0]),
           "inverter_sum_max_kw": round(float(el.inv_sum.max()), 2)}
    meter = frames_cache["meter_2017_2023"].set_index("ts").value
    b = el.assign(bk=el.ts.dt.floor("15min")).groupby("bk").inv_sum.agg(["mean", "count"])
    b = b[b["count"] == 3]
    j = pd.concat([meter.rename("meter"), b["mean"].rename("inv")], axis=1, join="inner")
    j = j[j.meter > 100]
    yearly = j.groupby(j.index.year).apply(
        lambda g: {"rows": len(g), "median_meter_over_inverter_sum": round(float((g.meter / g.inv).median()), 4),
                   "corr": round(float(g.meter.corr(g.inv)), 4)})
    out["meter_vs_inverter_sum_by_year"] = {int(k): v for k, v in yearly.items()}
    poa = frames_cache["poa_2017_2023"].set_index("ts").value.rename("poa")
    inv = el.set_index("ts").inv_sum
    inv = inv[~inv.index.duplicated()]
    lag = {}
    for season, lo, hi in (("2018-11_to_2019-02", "2018-11-04", "2019-03-01"),
                           ("2021-11_to_2022-02", "2021-11-08", "2022-03-01")):
        res = {}
        for k in range(-20, 25, 5):
            s = poa.copy()
            s.index = s.index + pd.Timedelta(minutes=k)
            jj = pd.concat([inv, s], axis=1, join="inner")
            jj = jj[(jj.index >= lo) & (jj.index < hi) & (jj.poa > 20)]
            res[k] = {"rows": len(jj), "corr": round(float(jj.inv_sum.corr(jj.poa)), 4)}
        lag[season] = res
    out["inverter_sum_vs_poa_corr_by_poa_label_shift_min"] = lag
    return out


frames_cache = {}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    profiles, frames, dst_rows = [], {}, []
    for name in FILES:
        p, df = profile(name)
        profiles.append(p)
        frames[name] = df
        dst_rows += dst_window_check(df, name)
    (OUT / "file_profiles.json").write_text(json.dumps(profiles, indent=2))
    pd.DataFrame(dst_rows).to_csv(OUT / "dst_transition_check.csv", index=False)

    meter_main = frames["meter_2017_2023"]
    poa_main = frames["poa_2017_2023"]
    noon = noon_timing(meter_main, poa_main)
    noon.to_csv(OUT / "noon_timing_2017_2023.csv", index=False)
    noon_2024 = noon_timing(frames["meter_2024"], frames["poa_2024"])
    noon_2025 = noon_timing(frames["meter_2025"], frames["poa_2025"])
    pd.concat([noon_2024.assign(files="2024"), noon_2025.assign(files="2025")]).to_csv(
        OUT / "noon_timing_2024_2025_files.csv", index=False)

    overlaps = [
        overlap_check(frames["meter_2024"], frames["meter_2025"], "meter_2024 vs meter_2025"),
        overlap_check(frames["poa_2024"], frames["poa_2025"], "poa_2024 vs poa_2025"),
        overlap_check(meter_main, frames["meter_2024"], "meter_2017_2023 vs meter_2024"),
        overlap_check(poa_main, frames["poa_2024"], "poa_2017_2023 vs poa_2024"),
    ]
    (OUT / "file_overlap_check.json").write_text(json.dumps(overlaps, indent=2))

    # Units/scale plausibility only (not PR): meter output at matched 15-min
    # timestamps vs mean POA in the same 15-min interval-start bucket.
    p15 = poa_main.dropna().assign(bucket=lambda x: x.ts.dt.floor("15min")).groupby("bucket").value.mean()
    m = meter_main.dropna().set_index("ts").value.groupby(level=0).first()
    j = pd.concat([m.rename("meter_kw"), p15.rename("poa_wm2")], axis=1, join="inner")
    hi = j[(j.poa_wm2 > 900) & (j.poa_wm2 < 1100)]
    scale = {"matched_15min_rows": len(j),
             "corr_meter_vs_poa": round(float(j.meter_kw.corr(j.poa_wm2)), 4),
             "rows_poa_900_1100": len(hi),
             "median_meter_kw_when_poa_900_1100": round(float(hi.meter_kw.median()), 1),
             "median_meter_kw_per_dc_kw_when_poa_900_1100": round(float(hi.meter_kw.median()) / DC_KW, 3),
             "meter_kw_gt_0_when_poa_eq_0": int(((j.meter_kw > 1) & (j.poa_wm2 <= 0)).sum()),
             "poa_gt_200_when_meter_le_0": int(((j.poa_wm2 > 200) & (j.meter_kw <= 0)).sum())}
    first_output = meter_main[meter_main.value > 1].ts.min()
    scale["first_meter_value_gt_1kw"] = str(first_output)
    first_poa = poa_main[poa_main.value > 1].ts.min()
    scale["first_poa_value_gt_1wm2"] = str(first_poa)
    yearly = meter_main.dropna().groupby(meter_main.ts.dt.year).value.agg(["count", "max"])
    scale["meter_rows_and_max_by_year"] = {int(k): {"rows": int(r["count"]), "max_kw": float(r["max"])}
                                          for k, r in yearly.iterrows()}
    yearly_p = poa_main.dropna().groupby(poa_main.ts.dt.year).value.agg(["count", "max"])
    scale["poa_rows_and_max_by_year"] = {int(k): {"rows": int(r["count"]), "max_wm2": float(r["max"])}
                                        for k, r in yearly_p.iterrows()}
    (OUT / "scale_plausibility.json").write_text(json.dumps(scale, indent=2))
    (OUT / "supplementary_checks.json").write_text(json.dumps(supplementary(frames), indent=2))
    (OUT / "gap_recount.json").write_text(json.dumps(genuine_gap_recount(frames), indent=2))
    (OUT / "daylight_zero_summary.json").write_text(json.dumps(daylight_zero_events(frames), indent=2))
    (OUT / "boundary_check_2024.json").write_text(json.dumps(boundary_check(), indent=2))
    (OUT / "poa_daytime_coverage.json").write_text(json.dumps(poa_daytime_coverage(frames), indent=2))
    frames_cache.update(frames)
    (OUT / "boundary_check_2017_2023.json").write_text(
        json.dumps(boundary_check_primary_window(), indent=2))
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
