"""Exact pairing of 15-min meter intervals with their three 5-min POA intervals (Phase 3).

Inputs are the Phase 2 resolved streams (expected_intervals + linked observations).
A meter interval [t, t+15) is paired only when it is inside the solar-geometry
reporting window, its meter observation is usable, and all three POA intervals
starting at t, t+5, t+10 exist, tile [t, t+15) exactly and are usable. Otherwise the
whole interval is excluded from both energy and irradiation, with a reason.
Nothing is interpolated, filled, scaled or substituted.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402
import timestamps as T  # noqa: E402

COVERAGE_TO_STATE = {"MISSING": "EXPECTED_ROW_MISSING", "DST_AMBIGUOUS": "TIME_AMBIGUOUS",
                     "CONFLICT_UNRESOLVED": "DUPLICATE_CONFLICT"}
STATE_ISSUE = {"EXPECTED_ROW_MISSING": "MISSING", "INVALID_PHYSICAL": "INVALID", "SOURCE_NULL": "SOURCE_NULL",
               "TIME_AMBIGUOUS": "TIME_AMBIGUOUS", "DUPLICATE_CONFLICT": "CONFLICT"}


def load_stream(processed, sensor_id, start_date, end_date):
    """Resolved stream for one sensor and local-date range [start_date, end_date)."""
    ei = pd.read_parquet(processed / "expected_intervals.parquet")
    ei = ei[(ei["sensor_id"] == sensor_id) & (ei["local_reporting_date"] >= start_date)
            & (ei["local_reporting_date"] < end_date)].copy()
    obs = pd.read_parquet(processed / "observations.parquet",
                          columns=["observation_id", "standardized_value", "standardized_unit", "observation_state"])
    obs = obs[obs["observation_id"].isin(set(ei["resolved_observation_id"].dropna()))]
    ei = ei.merge(obs, left_on="resolved_observation_id", right_on="observation_id", how="left")
    ei["state"] = np.where(ei["coverage_status"] == "COVERED", ei["resolved_observation_state"],
                           ei["coverage_status"].map(COVERAGE_TO_STATE))
    flags = pd.read_parquet(processed / "structural_quality_flags.parquet",
                            columns=["sensor_id", "flag_type", "rule_id", "interval_start_utc"])
    flags = flags[(flags["sensor_id"] == sensor_id) & flags["interval_start_utc"].isin(set(ei["interval_start_utc"]))]
    agg = (flags.assign(tok=flags["flag_type"] + ":" + flags["rule_id"]).groupby("interval_start_utc")["tok"]
           .agg(lambda s: ";".join(sorted(s))))
    ei["flags"] = ei["interval_start_utc"].map(agg).fillna("")
    return ei.rename(columns={"standardized_value": "value", "standardized_unit": "unit"})[
        ["interval_start_utc", "interval_end_utc", "local_reporting_date", "local_day_length_h",
         "resolved_observation_id", "state", "value", "unit", "flags"]].reset_index(drop=True)


def add_window_and_clearsky(meter, site, cfg):
    """Solar-geometry reporting window and clear-sky POA weight per meter interval."""
    mid = meter["interval_start_utc"] + (meter["interval_end_utc"] - meter["interval_start_utc"]) / 2
    hours = (meter["interval_end_utc"] - meter["interval_start_utc"]).dt.total_seconds() / 3600
    elev = T.solar_elevation_deg(mid, site["latitude"], site["longitude"], site["elevation_m"])
    loc = pvlib.location.Location(site["latitude"], site["longitude"], tz="UTC", altitude=site["elevation_m"])
    idx = pd.DatetimeIndex(mid)
    sp = loc.get_solarposition(idx)
    cs = loc.get_clearsky(idx, model=cfg["clearsky_model"]["model"], solar_position=sp)
    poa = pvlib.irradiance.get_total_irradiance(site["tilt_deg"], site["azimuth_deg"], sp["apparent_zenith"],
                                                sp["azimuth"], cs["dni"], cs["ghi"], cs["dhi"],
                                                model=cfg["clearsky_model"]["transposition"])["poa_global"]
    out = meter.copy()
    out["solar_elevation_deg"] = elev
    out["in_window"] = elev > cfg["reporting_window"]["min_solar_elevation_deg"]
    out["clearsky_kwh_m2"] = np.where(out["in_window"], poa.fillna(0).clip(lower=0).to_numpy() / 1000 * hours, 0.0)
    return out


def _require_unique(frame, name):
    dup = frame["interval_start_utc"].duplicated(keep=False)
    if dup.any():
        raise ValueError(f"{name}: {int(dup.sum())} rows share an interval start; resolved streams must be unique")


def pair_intervals(meter, poa, cfg):
    """One row per meter interval with pairing result, contributions and exclusion reason.

    meter needs: interval_start_utc, interval_end_utc, local_reporting_date, local_day_length_h,
    resolved_observation_id, state, value, unit, flags, in_window, clearsky_kwh_m2.
    poa needs: interval_start_utc, interval_end_utc, resolved_observation_id, state, value, unit, flags.
    """
    _require_unique(meter, "meter")
    _require_unique(poa, "poa")
    usable = set(cfg["pairing"]["usable_states"])
    units = cfg["units"]
    p = poa.set_index("interval_start_utc")
    out = meter.rename(columns={"resolved_observation_id": "meter_observation_id", "state": "meter_state",
                                "value": "meter_kw_value", "unit": "meter_unit", "flags": "meter_flags"}).copy()
    out["interval_hours"] = (out["interval_end_utc"] - out["interval_start_utc"]).dt.total_seconds() / 3600
    starts, ends, ok_each, issues, h_sub = [], [], [], [], []
    for i, off in enumerate(cfg["pairing"]["poa_offsets_min"]):
        key = out["interval_start_utc"] + pd.Timedelta(minutes=off)
        sub = p.reindex(pd.DatetimeIndex(key))
        present = sub["interval_end_utc"].notna().to_numpy()
        out[f"poa{i}_observation_id"] = sub["resolved_observation_id"].to_numpy()
        out[f"poa{i}_state"] = np.where(present, sub["state"].to_numpy(), "NOT_EXPECTED")
        out[f"poa{i}_wm2_value"] = sub["value"].to_numpy()
        out[f"poa{i}_flags"] = sub["flags"].fillna("").to_numpy()
        key_ix, end_ix = pd.DatetimeIndex(key), pd.DatetimeIndex(sub["interval_end_utc"])
        starts.append(key_ix.asi8)                         # exact integer-nanosecond boundaries
        ends.append(np.where(end_ix.isna(), np.iinfo(np.int64).min, end_ix.asi8))
        st = out[f"poa{i}_state"]
        ok_each.append(st.isin(usable).to_numpy() & np.isfinite(sub["value"].to_numpy(dtype=float)))
        issues.append(st.map(STATE_ISSUE).to_numpy())
        hrs = (end_ix - key_ix).total_seconds().to_numpy() / 3600
        factor = np.array([M.unit_factor(u, units["irradiance_to_kw_m2"]) if isinstance(u, str) else np.nan
                           for u in sub["unit"]])
        h_sub.append(sub["value"].to_numpy(dtype=float) * factor * hrs)

    # Boundaries must tile [t, t+15) exactly: t = s0, e0 = s1, e1 = s2, e2 = meter end.
    aligned = starts[0] == pd.DatetimeIndex(out["interval_start_utc"]).asi8
    for i in range(len(starts) - 1):
        aligned &= ends[i] == starts[i + 1]
    aligned &= ends[-1] == pd.DatetimeIndex(out["interval_end_utc"]).asi8
    out["poa_aligned"] = aligned

    meter_ok = out["meter_state"].isin(usable).to_numpy() & np.isfinite(out["meter_kw_value"].to_numpy(dtype=float))
    poa_ok = np.logical_and.reduce(ok_each) & aligned
    out["n_poa_usable"] = np.sum(ok_each, axis=0)
    meter_issue = out["meter_state"].map(STATE_ISSUE)
    poa_issue = pd.Series(np.where(~aligned, "MISALIGNED", None), index=out.index, dtype="object")
    for code in ("MISSING", "INVALID", "SOURCE_NULL", "TIME_AMBIGUOUS", "CONFLICT"):   # first match wins
        hit = np.any([iss == code for iss in issues], axis=0) & poa_issue.isna().to_numpy()
        poa_issue[hit] = code
    out["meter_issue"] = meter_issue
    out["poa_issue"] = poa_issue

    in_window = out["in_window"].to_numpy()
    out["exclusion_reason"] = np.select(
        [~in_window, ~meter_ok, ~poa_ok],
        ["OUTSIDE_WINDOW", "METER_" + meter_issue.fillna("UNUSABLE").astype(str),
         "POA_" + poa_issue.fillna("UNUSABLE").astype(str)], default=None)
    out["exclusion_reason"] = out["exclusion_reason"].replace({"None": None})
    out["paired"] = in_window & meter_ok & poa_ok

    meter_factor = np.array([M.unit_factor(u, units["power_to_kw"]) if isinstance(u, str) else np.nan
                             for u in out["meter_unit"]])
    e = out["meter_kw_value"].to_numpy(dtype=float) * meter_factor * out["interval_hours"].to_numpy()
    h = np.sum(h_sub, axis=0)
    out["e_ac_kwh"] = np.where(out["paired"], e, np.nan)
    out["h_poa_kwh_m2"] = np.where(out["paired"], h, np.nan)
    vals = np.column_stack([out["meter_kw_value"]] + [out[f"poa{i}_wm2_value"] for i in range(len(starts))])
    out["negative_input"] = in_window & np.any(np.nan_to_num(vals.astype(float), nan=0.0) < 0, axis=1)
    return out
