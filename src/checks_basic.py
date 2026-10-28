"""Shared prerequisites and basic-method checks (experiment-v2), evaluated per local day.

Every check returns a result row with: check_id, check_class, outcome (PASS / FLAGGED /
INCONCLUSIVE), reason_code, action (INSUFFICIENT_DATA / REVIEW_REQUIRED / CONTEXT / NONE),
measured, threshold, unit, affected_intervals, evidence. Inputs are current-input
evidence from evidence.build_evidence, never Phase 2 flags.
"""
import json

import numpy as np
import pandas as pd

PASS, FLAGGED, INCONCLUSIVE = "PASS", "FLAGGED", "INCONCLUSIVE"
POA_COLS = ["poa0_wm2_value", "poa1_wm2_value", "poa2_wm2_value"]
USABLE = {"OBSERVED_VALID", "OBSERVED_ZERO"}
MAX_LISTED = 60


def result(day, check_id, check_class, outcome, reason_code, action, measured=None, threshold=None, unit="",
           affected=(), evidence=None):
    aff = [pd.Timestamp(t).strftime("%Y-%m-%dT%H:%MZ") for t in affected]
    return {"local_reporting_date": day, "check_id": check_id, "check_class": check_class, "outcome": outcome,
            "reason_code": reason_code if outcome == FLAGGED or action == "CONTEXT" else "",
            "action": action if outcome == FLAGGED or action == "CONTEXT" else "NONE",
            "measured": measured, "threshold": threshold, "unit": unit,
            "n_affected_intervals": len(aff),
            "affected_intervals": ";".join(aff[:MAX_LISTED]) + (";..." if len(aff) > MAX_LISTED else ""),
            "evidence": json.dumps(evidence or {}, default=str)}


def shared_checks(day, dm, capacity_ok):
    """S1 capacity, S5 paired data / PR availability, S6 minimum denominator."""
    out = [result(day, "S1", "shared", PASS if capacity_ok else FLAGGED, "INVALID_CAPACITY", "INSUFFICIENT_DATA",
                  capacity_ok, "finite and > 0", "kW")]
    if dm["window_intervals"] == 0:
        out.append(result(day, "S5", "shared", FLAGGED, "NO_WINDOW_INTERVALS", "INSUFFICIENT_DATA", 0, ">= 1", "intervals"))
    else:
        out.append(result(day, "S5", "shared", PASS if dm["paired_intervals"] > 0 else FLAGGED, "NO_PAIRED_DATA",
                          "INSUFFICIENT_DATA", dm["paired_intervals"], ">= 1", "paired intervals"))
    if dm["paired_intervals"] > 0:
        h = dm["h_poa_kwh_m2"]
        out.append(result(day, "S6", "shared", PASS if h >= 0.5 else FLAGGED, "LOW_IRRADIATION", "INSUFFICIENT_DATA",
                          round(h, 6), ">= 0.5", "kWh/m2"))
        avail = np.isfinite(dm["diagnostic_pr"]) or not capacity_ok
        out.append(result(day, "S5b", "shared", PASS if avail else FLAGGED, "PR_UNAVAILABLE", "INSUFFICIENT_DATA",
                          dm["diagnostic_pr"], "finite PR", "ratio"))
    else:
        out.append(result(day, "S6", "shared", INCONCLUSIVE, "LOW_IRRADIATION", "NONE", None, ">= 0.5", "kWh/m2",
                          evidence={"note": "not evaluated: no paired data"}))
    return out


def basic_checks(day, ev_day, dm, cfg, meta):
    """B1-B5d for one day. ev_day holds that day's slices of the evidence tables."""
    b = cfg["methods"]["basic"]
    z = b["zero_output_policy"]
    pairs, meter, poa = ev_day["pairs"], ev_day["meter"], ev_day["poa"]
    w = pairs[pairs["in_window"]]
    p = w[w["paired"]]
    out = []

    # B1 time coverage
    if dm["paired_intervals"] > 0:
        cov = dm["time_coverage"]
        excl = w[~w["paired"]]
        out.append(result(day, "B1", "basic", PASS if cov >= b["min_time_coverage"] else FLAGGED, "LOW_TIME_COVERAGE",
                          "INSUFFICIENT_DATA", round(cov, 6), f">= {b['min_time_coverage']}", "fraction of window hours",
                          excl["interval_start_utc"],
                          {"window_hours": dm["window_hours"], "paired_hours": dm["paired_hours"],
                           "exclusions": excl["exclusion_reason"].value_counts().to_dict()}))
    else:
        out.append(result(day, "B1", "basic", INCONCLUSIVE, "LOW_TIME_COVERAGE", "NONE", None,
                          f">= {b['min_time_coverage']}", "fraction", evidence={"note": "not evaluated: no paired data"}))

    # B2 range: invalid values and negatives inside the window (meter or any POA sub-interval)
    win_starts = set(w["interval_start_utc"])
    m_inv = meter[meter["in_window"] & (meter["state"] == "INVALID_PHYSICAL")]
    p_inv = poa[poa["in_window"] & (poa["state"] == "INVALID_PHYSICAL")]
    inv = pd.concat([m_inv["interval_start_utc"], p_inv["interval_start_utc"]])
    out.append(result(day, "B2", "basic", FLAGGED if len(inv) else PASS, "INVALID_INPUT_IN_WINDOW", "REVIEW_REQUIRED",
                      len(inv), "0", "invalid readings in window", inv,
                      {"meter_values": m_inv["value"].tolist(), "poa_values": p_inv["value"].tolist()}))
    neg = w[w["negative_input"]]
    out.append(result(day, "B2b", "basic", FLAGGED if len(neg) else PASS, "NEGATIVE_INPUT_IN_WINDOW",
                      "REVIEW_REQUIRED", len(neg), "0", "intervals with a negative reading", neg["interval_start_utc"]))

    # B3 duplicates / conflicts inside the window (recomputed from supplied rows)
    dup_m = meter[meter["in_window"] & (meter["n_rows"] > 1)]
    dup_p = poa[poa["in_window"] & (poa["n_rows"] > 1)]
    dup = pd.concat([dup_m["interval_start_utc"], dup_p["interval_start_utc"]])
    out.append(result(day, "B3", "basic", FLAGGED if len(dup) else PASS, "UNRESOLVED_TIME_IN_WINDOW",
                      "REVIEW_REQUIRED", len(dup), "0", "duplicated or conflicting intervals in window", dup,
                      {"conflicting": int(dup_m["conflict"].sum() + dup_p["conflict"].sum())}))

    # B4 sampling regularity: off-grid rows inside the window
    offs = [o[o["in_window"].fillna(False).astype(bool)] for o in ev_day["off_grid"].values()]
    off = pd.concat([o["interval_start_utc"] for o in offs]) if offs else pd.Series(dtype="datetime64[ns, UTC]")
    out.append(result(day, "B4", "basic", FLAGGED if len(off) else PASS, "IRREGULAR_SAMPLING", "REVIEW_REQUIRED",
                      len(off), "0", "off-grid readings in window", off))

    # B5a catch-up (Phase 2 A-CATCHUP rule recomputed on current inputs)
    out.append(catch_up(day, meter, poa, cfg, meta["ac_power"]["max"]))

    # B5b suspicious meter zero; B5c material zero output; B5d suspicious POA zero
    poa_mean = p[POA_COLS].mean(axis=1)
    zeros = p[p["meter_state"] == "OBSERVED_ZERO"]
    susp = zeros[poa_mean.loc[zeros.index] >= z["suspicious_meter_zero_min_poa_wm2"]]
    out.append(result(day, "B5b", "basic", FLAGGED if len(susp) else PASS, "SUSPICIOUS_ZERO_OUTPUT",
                      "REVIEW_REQUIRED", len(susp), f"0 zeros with POA mean >= {z['suspicious_meter_zero_min_poa_wm2']}",
                      "intervals", susp["interval_start_utc"],
                      {"poa_mean_wm2": poa_mean.loc[susp.index].round(1).tolist()}))
    h_day = p["h_poa_kwh_m2"].sum()
    share = float(zeros["h_poa_kwh_m2"].sum() / h_day) if h_day > 0 else 0.0
    out.append(result(day, "B5c", "basic", FLAGGED if share >= z["material_zero_share_of_h"] else PASS,
                      "MATERIAL_ZERO_OUTPUT", "REVIEW_REQUIRED", round(share, 6), f">= {z['material_zero_share_of_h']}",
                      "share of paired H in zero-output intervals (sensitivity, not an error bound)",
                      zeros["interval_start_utc"], {"n_zero_intervals": len(zeros)}))
    cs_mean = p["clearsky_kwh_m2"] / p["interval_hours"] * 1000
    pz = p[(p[POA_COLS] == 0).any(axis=1) & (cs_mean >= z["suspicious_poa_zero_min_clearsky_wm2"])]
    out.append(result(day, "B5d", "basic", FLAGGED if len(pz) else PASS, "SUSPICIOUS_POA_ZERO", "REVIEW_REQUIRED",
                      len(pz), f"0 POA zeros with clear-sky >= {z['suspicious_poa_zero_min_clearsky_wm2']}",
                      "intervals", pz["interval_start_utc"]))
    return out


def catch_up(day, meter, poa, cfg, upper):
    """Invalid-high meter value directly after >= 1 contiguous daylight zeros (spike or zeros in window)."""
    rule = cfg["methods"]["basic"]["catch_up_recomputed"]
    m = meter.sort_values("interval_start_utc").reset_index(drop=True)
    poa_u = poa[poa["state"].isin(USABLE)]
    bright = set(poa_u.loc[poa_u["value"] > 200, "meter_start"])
    daylight_zero = (m["state"] == "OBSERVED_ZERO") & ((m["solar_elevation_deg"] > 10)
                                                      | m["interval_start_utc"].isin(bright))
    hits = []
    for i in m.index[(m["state"] == "INVALID_PHYSICAL") & (m["value"] > upper)]:
        j, run = i - 1, []
        while j >= 0 and daylight_zero[j] and m.loc[j + 1, "interval_start_utc"] - m.loc[j, "interval_start_utc"] \
                == pd.Timedelta("15min"):
            run.append(j)
            j -= 1
        if run and (m.loc[[i] + run, "in_window"]).any():
            hits += [m.loc[i, "interval_start_utc"]] + list(m.loc[run, "interval_start_utc"])
    return result(day, "B5a", "basic", FLAGGED if hits else PASS, "SUSPECTED_CATCH_UP", "REVIEW_REQUIRED",
                  len(hits), "0", "spike and preceding daylight-zero intervals", hits, {"rule": rule})

