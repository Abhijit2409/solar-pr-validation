"""Deterministic fault injection on copies of detector-input observations (experiment-v3, A8-A13).

Each function returns a NEW frame plus a truth record; the input frame is never mutated.
Truth records (case id, window, scenario, severity, seed, placement, affected rows,
collisions, status) stay outside the detector. reassign_refs() gives every frame the same
opaque obs_ref format (r + 7 digits) so references carry no fault information.

Scenarios (config faults):
  missing_daytime  primary rows_removed [30,60,120,240] | secondary explicit_nulls [120]
  timestamp_shift  primary POA-only [-60,-30,-15,15,30,60] | secondary whole_file [-60,-30,30,60]
  flatline_poa     primary hold_last [15,30,60,120,240] | secondary fixed_value_600 [120]
"""
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as E  # noqa: E402
import timestamps as T  # noqa: E402

POA, METER = E.POA, E.METER
APPLIED, UNSCORABLE = "APPLIED", "UNSCORABLE"


def window_hash(window_id):
    return int.from_bytes(hashlib.sha256(window_id.encode("utf-8")).digest()[:8], "big")


def stable_rng(family_seed, window_id):
    """Order-independent per-window generator (A13)."""
    return np.random.default_rng([int(family_seed), window_hash(window_id)])


def day_geometry(day, site, metric_cfg):
    g = E.meter_grid([day], site, metric_cfg)
    return g[["interval_start_utc", "interval_end_utc", "in_window", "solar_elevation_deg"]]


def missing_centre(geom, rng, max_minutes=240):
    """A9: in-window meter start c with every meter interval in [c - max/2, c + max/2) in-window."""
    win = set(geom.loc[geom["in_window"], "interval_start_utc"])
    half = pd.Timedelta(minutes=max_minutes // 2)
    steps = [pd.Timedelta(minutes=15 * k) for k in range(-(max_minutes // 30), max_minutes // 30)]
    cands = sorted(c for c in win if all(c + s in win for s in steps))
    if not cands:
        return None, 0
    return cands[int(rng.integers(len(cands)))], len(cands)


def flatline_start(geom, rng, site, max_minutes=240, min_elevation=15.0):
    """A11: POA start t in an in-window meter interval, elevation(t + 2.5 min) >= min, [t, t+max) in-window."""
    win = set(geom.loc[geom["in_window"], "interval_start_utc"])
    starts = pd.DatetimeIndex(sorted(m + pd.Timedelta(minutes=k) for m in win for k in (0, 5, 10)))
    if len(starts) == 0:
        return None, 0
    elev = T.solar_elevation_deg(starts + pd.Timedelta("2min30s"), site["latitude"], site["longitude"],
                                 site["elevation_m"])
    n = max_minutes // 5
    cands = [t for t, e in zip(starts, elev)
             if e >= min_elevation and all((t + pd.Timedelta(minutes=5 * k)).floor("15min") in win for k in range(n))]
    if not cands:
        return None, 0
    return cands[int(rng.integers(len(cands)))], len(cands)


def case_specs(family, exp):
    """All (scenario, severity) pairs for a family, primary first."""
    f = exp["faults"][family]
    if family == "missing_daytime":
        return [("primary:rows_removed", d) for d in f["primary"]["durations_min"]] + \
               [("secondary:explicit_nulls", d) for d in f["secondary"]["durations_min"]]
    if family == "timestamp_shift":
        return [("primary:poa_only", s) for s in f["primary"]["shifts_min"]] + \
               [("secondary:whole_file", s) for s in f["secondary"]["shifts_min"]]
    if family == "flatline_poa":
        return [("primary:hold_last", d) for d in f["primary"]["durations_min"]] + \
               [("secondary:fixed_value_600", d) for d in f["secondary"]["durations_min"]]
    raise ValueError(family)


def plan_window(window_id, family, day, site, metric_cfg, exp):
    """One placement draw per window, shared by every severity of the family (A13)."""
    seed = exp["faults"][family]["primary"]["seed"]
    rng = stable_rng(seed, window_id)
    geom = day_geometry(day, site, metric_cfg)
    plan = {"window_id": window_id, "family": family, "local_reporting_date": day, "family_seed": seed,
            "window_hash": window_hash(window_id), "placement": None, "n_candidates": 0}
    if family == "missing_daytime":
        plan["placement"], plan["n_candidates"] = missing_centre(geom, rng)
    elif family == "flatline_poa":
        plan["placement"], plan["n_candidates"] = flatline_start(geom, rng, site)
    else:
        plan["placement"] = "whole block [D-1, D+2)"
    return plan


def _truth(plan, scenario, severity, status, reason="", **kw):
    t = {"case_id": f"{plan['window_id']}|{scenario}|{severity:+d}", "window_id": plan["window_id"],
         "family": plan["family"], "scenario": scenario, "severity": severity,
         "local_reporting_date": plan["local_reporting_date"], "family_seed": plan["family_seed"],
         "window_hash": plan["window_hash"], "placement": str(plan["placement"]),
         "n_candidates": plan["n_candidates"], "injection_status": status, "unscorable_reason": reason,
         "block_start_utc": "", "block_end_utc": "", "n_rows_affected": 0, "n_values_changed": 0,
         "n_collisions": 0, "affected_refs": "", "collisions": "", "hold_value": None}
    t.update(kw)
    return t


def _in_block(frame, signal, start, end):
    return (frame["signal"] == signal) & (frame["interval_start_utc"] >= start) & (frame["interval_start_utc"] < end)


def apply_case(obs, plan, scenario, severity, site, meta, exp):
    """Return (faulted copy, truth). The input frame is not modified."""
    out = obs.copy(deep=True)
    if out.empty:
        return out, _truth(plan, scenario, severity, UNSCORABLE, "empty_input")
    family = plan["family"]
    if family in ("missing_daytime", "flatline_poa") and plan["placement"] is None:
        return out, _truth(plan, scenario, severity, UNSCORABLE, "no_placement")

    if family == "missing_daytime":
        c = plan["placement"]
        start, end = c - pd.Timedelta(minutes=severity // 2), c + pd.Timedelta(minutes=severity // 2)
        hit = _in_block(out, POA, start, end)
        refs = out.loc[hit, "obs_ref"].tolist()
        if scenario.endswith("rows_removed"):
            out = out[~hit].copy()
            changed = len(refs)
        else:
            changed = int(out.loc[hit, "value"].notna().sum())
            out.loc[hit, "value"] = np.nan
        status, reason = (APPLIED, "") if changed else (UNSCORABLE, "no_effect")
        return out, _truth(plan, scenario, severity, status, reason, block_start_utc=str(start),
                           block_end_utc=str(end), n_rows_affected=len(refs), n_values_changed=changed,
                           affected_refs=";".join(refs))

    if family == "timestamp_shift":
        tz = site["local_timezone"]
        day = pd.Timestamp(plan["local_reporting_date"])
        dates = {(day + pd.Timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)}
        signals = [POA] if scenario.startswith("primary") else [POA, METER]
        local = out["interval_start_utc"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
        hit = out["signal"].isin(signals) & out["interval_start_utc"].notna() & local.isin(dates)
        refs = out.loc[hit, "obs_ref"].tolist()
        out.loc[hit, "interval_start_utc"] = out.loc[hit, "interval_start_utc"] + pd.Timedelta(minutes=severity)
        collisions = []
        for s in signals:
            moved = out[hit & (out["signal"] == s)]
            fixed = set(out.loc[~hit & (out["signal"] == s), "interval_start_utc"])
            collisions += [f"{s}@{t:%Y-%m-%dT%H:%MZ}" for t in moved["interval_start_utc"] if t in fixed]
        status, reason = (APPLIED, "") if refs else (UNSCORABLE, "no_effect")
        return out, _truth(plan, scenario, severity, status, reason, block_start_utc=min(dates),
                           block_end_utc=max(dates), n_rows_affected=len(refs), n_values_changed=0,
                           n_collisions=len(collisions), collisions=";".join(collisions),
                           affected_refs=";".join(refs))

    # flatline_poa
    t0 = plan["placement"]
    end = t0 + pd.Timedelta(minutes=severity)
    if scenario.endswith("hold_last"):
        tz = site["local_timezone"]
        b = meta[POA]
        poa = out[(out["signal"] == POA) & out["interval_start_utc"].notna()]
        on_day = poa["interval_start_utc"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d") == plan["local_reporting_date"]
        usable = poa["value"].notna() & (poa["value"] >= b["min"]) & (poa["value"] <= b["max"])
        prior = poa[on_day & usable & (poa["interval_start_utc"] < t0)].sort_values("interval_start_utc")
        if prior.empty:
            return out, _truth(plan, scenario, severity, UNSCORABLE, "no_hold_value")
        value = float(prior["value"].iloc[-1])
    else:
        value = float(exp["injection"]["flatline_poa"]["secondary_value_wm2"])
    hit = _in_block(out, POA, t0, end)
    before = out.loc[hit, "value"]
    changed = int((before.isna() | (before - value).abs().gt(1e-12)).sum())
    refs = out.loc[hit, "obs_ref"].tolist()
    out.loc[hit, "value"] = value
    status, reason = (APPLIED, "") if changed else (UNSCORABLE, "no_effect")
    return out, _truth(plan, scenario, severity, status, reason, block_start_utc=str(t0), block_end_utc=str(end),
                       n_rows_affected=len(refs), n_values_changed=changed, affected_refs=";".join(refs),
                       hold_value=value)


def reassign_refs(frame, prefix="r"):
    """Fresh opaque refs in one format for every copy; returns (detector frame, map new_ref -> previous ref)."""
    f = frame.sort_values(["signal", "interval_start_utc", "obs_ref"], na_position="last",
                          kind="mergesort").reset_index(drop=True)
    new = [f"{prefix}{i:07d}" for i in range(len(f))]
    ref_map = pd.DataFrame({"obs_ref": new, "previous_ref": f["obs_ref"].to_numpy()})
    f = f.assign(obs_ref=new)
    return f[E.DETECTOR_INPUT_COLUMNS], ref_map
