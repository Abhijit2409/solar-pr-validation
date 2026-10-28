"""Current-input evidence for the Phase 5 detectors.

Everything a check needs is rebuilt from the observations actually supplied, so an
injected copy changes the evidence the same way real data would:
expected UTC grid -> solar-geometry window and clear-sky weights -> per-interval state
from values (missing / null / invalid / zero / valid, duplicates, off-grid rows) ->
exact 3-to-1 pairing (shared Phase 3 code) -> shared day metrics.

Detector input contract: a DataFrame with exactly DETECTOR_INPUT_COLUMNS. obs_ref is an
opaque identifier; source files, rows, Phase 2 states, flags and any injection
information are deliberately absent.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402
import pair_intervals as P  # noqa: E402

DETECTOR_INPUT_COLUMNS = ["obs_ref", "signal", "interval_start_utc", "value", "unit"]
METER, POA = "ac_power", "poa_irradiance"
DUP_TOL = 1e-6


def measurement_meta(data_structure_cfg):
    """Nominal cadence, unit and screening bounds per signal (from config/data_structure.yaml)."""
    lim = data_structure_cfg["physical_limits"]
    units = {s["signal"]: s["standardized_unit"] for s in data_structure_cfg["sensors"].values()}
    return {METER: {"step_min": 15, "unit": units[METER], "min": lim[METER]["min"], "max": lim[METER]["max"]},
            POA: {"step_min": 5, "unit": units[POA], "min": lim[POA]["min"], "max": lim[POA]["max"]}}


def validate_input(obs):
    """Reject inputs that carry anything beyond the contract (e.g. labels or lineage)."""
    if list(obs.columns) != DETECTOR_INPUT_COLUMNS:
        raise ValueError(f"detector input must have exactly {DETECTOR_INPUT_COLUMNS}, got {list(obs.columns)}")
    if obs["obs_ref"].duplicated().any():
        raise ValueError("obs_ref must be unique")
    if not set(obs["signal"].dropna()) <= {METER, POA}:
        raise ValueError(f"unknown signals {set(obs['signal']) - {METER, POA}}")


def meter_grid(dates, site, metric_cfg):
    """15-min expected meter intervals for the given local dates, with window and clear-sky weights."""
    tz = site["local_timezone"]
    parts = []
    for d in sorted(set(dates)):
        start = pd.Timestamp(d).tz_localize(tz).tz_convert("UTC")
        end = (pd.Timestamp(d) + pd.Timedelta(days=1)).tz_localize(tz).tz_convert("UTC")
        starts = pd.date_range(start, end, freq="15min", inclusive="left")
        parts.append(pd.DataFrame({"interval_start_utc": starts, "local_reporting_date": d,
                                   "local_day_length_h": (end - start).total_seconds() / 3600}))
    g = pd.concat(parts, ignore_index=True)
    g["interval_end_utc"] = g["interval_start_utc"] + pd.Timedelta("15min")
    g = P.add_window_and_clearsky(g, site, metric_cfg)
    return g


def value_state(values, bounds):
    v = pd.Series(values, dtype=float)
    state = np.where(v.isna(), "SOURCE_NULL",
                     np.where((v < bounds["min"]) | (v > bounds["max"]), "INVALID_PHYSICAL",
                              np.where(v == 0, "OBSERVED_ZERO", "OBSERVED_VALID")))
    return pd.Series(state, index=v.index)


def resolve(obs_signal, grid_starts, step_min, bounds):
    """Map supplied rows onto expected interval starts. Returns (resolved grid, off-grid rows, unplaced rows)."""
    rows = obs_signal.copy()
    unplaced = rows[rows["interval_start_utc"].isna()]
    rows = rows[rows["interval_start_utc"].notna()]
    grid_set = pd.DatetimeIndex(grid_starts)
    on = rows["interval_start_utc"].isin(grid_set)
    lo, hi = grid_set.min(), grid_set.max() + pd.Timedelta(minutes=step_min)
    off = rows[~on & (rows["interval_start_utc"] >= lo) & (rows["interval_start_utc"] < hi)]
    rows = rows[on].sort_values(["interval_start_utc", "obs_ref"], kind="mergesort")
    g = rows.groupby("interval_start_utc", sort=False)
    first = g.first()
    n = g.size()
    vmax, vmin = g["value"].max(), g["value"].min()
    nnull = g["value"].apply(lambda s: s.isna().sum())
    conflict = (n > 1) & (((vmax - vmin) > DUP_TOL) | ((nnull > 0) & (nnull < n)))
    out = pd.DataFrame({"interval_start_utc": grid_set})
    out = out.merge(pd.DataFrame({"obs_ref": first["obs_ref"], "value": first["value"], "unit": first["unit"],
                                  "n_rows": n, "conflict": conflict}).reset_index(),
                    on="interval_start_utc", how="left")
    out["n_rows"] = out["n_rows"].fillna(0).astype(int)
    out["conflict"] = out["conflict"].fillna(False).astype(bool)
    state = value_state(out["value"], bounds)
    state[out["n_rows"] == 0] = "EXPECTED_ROW_MISSING"
    state[out["conflict"]] = "DUPLICATE_CONFLICT"
    out["state"] = state
    out["interval_end_utc"] = out["interval_start_utc"] + pd.Timedelta(minutes=step_min)
    return out, off, unplaced


def build_evidence(obs, site, meta, metric_cfg, dates):
    """All current-input evidence for the given local reporting dates."""
    validate_input(obs)
    mg = meter_grid(dates, site, metric_cfg)
    m_res, m_off, m_unpl = resolve(obs[obs["signal"] == METER], mg["interval_start_utc"], 15, meta[METER])
    meter = mg.merge(m_res, on=["interval_start_utc", "interval_end_utc"], how="left")
    poa_starts = (mg["interval_start_utc"].to_numpy()[:, None] +
                  np.array([pd.Timedelta(minutes=k) for k in metric_cfg["pairing"]["poa_offsets_min"]])).ravel()
    p_res, p_off, p_unpl = resolve(obs[obs["signal"] == POA], pd.DatetimeIndex(poa_starts).sort_values(), 5, meta[POA])
    for frame in (meter, p_res):
        frame["resolved_observation_id"] = frame["obs_ref"]
        frame["flags"] = ""
    pairs = P.pair_intervals(meter, p_res, metric_cfg)
    p_res["meter_start"] = p_res["interval_start_utc"].dt.floor("15min")
    p_res = p_res.merge(mg[["interval_start_utc", "local_reporting_date", "in_window"]]
                        .rename(columns={"interval_start_utc": "meter_start"}), on="meter_start", how="left")

    def place(off):
        off = off.copy()
        off["meter_start"] = off["interval_start_utc"].dt.floor("15min")
        return off.merge(mg[["interval_start_utc", "local_reporting_date", "in_window"]]
                         .rename(columns={"interval_start_utc": "meter_start"}), on="meter_start", how="left")

    return {"meter": meter, "poa": p_res, "pairs": pairs,
            "off_grid": {METER: place(m_off), POA: place(p_off)},
            "unplaced_rows": {METER: len(m_unpl), POA: len(p_unpl)}}


def shared_day_metrics(pairs, capacity_kw, g_stc):
    """Shared arithmetic per day (identical for both methods): coverage, E_AC, H_POA, diagnostic PR."""
    rows = []
    cap_ok = capacity_kw is not None and np.isfinite(float(capacity_kw)) and float(capacity_kw) > 0
    for day, d in pairs.groupby("local_reporting_date", sort=True):
        w = d[d["in_window"]]
        p = w[w["paired"]]
        wh, ph = float(w["interval_hours"].sum()), float(p["interval_hours"].sum())
        cs_w, cs_p = float(w["clearsky_kwh_m2"].sum()), float(p["clearsky_kwh_m2"].sum())
        e = float(p["e_ac_kwh"].sum()) if len(p) else float("nan")
        h = float(p["h_poa_kwh_m2"].sum()) if len(p) else float("nan")
        rows.append({"local_reporting_date": day, "window_intervals": len(w), "window_hours": wh,
                     "paired_intervals": len(p), "paired_hours": ph,
                     "time_coverage": ph / wh if wh > 0 else float("nan"),
                     "clearsky_weighted_coverage": cs_p / cs_w if cs_w > 0 else float("nan"),
                     "e_ac_kwh": e, "h_poa_kwh_m2": h, "capacity_ok": cap_ok,
                     "diagnostic_pr": M.performance_ratio(e, capacity_kw, h, g_stc) if (len(p) and cap_ok)
                     else float("nan")})
    return pd.DataFrame(rows)
