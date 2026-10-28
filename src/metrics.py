"""Performance Ratio arithmetic and daily calculation/input eligibility (Phase 3).

PR = (E_AC / P_DC_STC) / (H_POA / G_STC), stored as a ratio. Unavailable PR is NaN
(written as null), never 0 or infinity. Statuses here are calculation/input
eligibility only; the Phase 5 detectors are separate.
"""
import math

import numpy as np
import pandas as pd

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
CHECKS_PASSED = "CHECKS_PASSED"

# Withholding flag (type:rule) -> day-level review reason code.
REVIEW_REASON = {
    "DAYLIGHT_ZERO_UNRESOLVED:A-DAYZERO": "UNRESOLVED_ZERO_OUTPUT",
    "SUSPECTED_LOGGER_ARTEFACT:A-CATCHUP": "SUSPECTED_CATCH_UP",
    "INVALID_VALUE:R-LIMITS": "INVALID_INPUT_IN_WINDOW",
    "UNRESOLVED_DST_INTERVAL:T-AMBIG": "UNRESOLVED_TIME_IN_WINDOW",
    "AMBIGUOUS_LOCAL_TIMESTAMP:T-AMBIG": "UNRESOLVED_TIME_IN_WINDOW",
}


def unit_factor(unit, table):
    """Multiplier to the target unit; unknown units raise rather than guess."""
    if unit not in table:
        raise ValueError(f"unknown unit {unit!r}; known: {sorted(table)}")
    return table[unit]


def energy_kwh(power, unit, hours, power_to_kw):
    """Interval energy (kWh) from interval-average power in `unit` over `hours`."""
    return power * unit_factor(unit, power_to_kw) * hours


def irradiation_kwh_m2(irradiance, unit, hours, irradiance_to_kw_m2):
    """Interval irradiation (kWh/m2) from interval-average irradiance in `unit` over `hours`."""
    return irradiance * unit_factor(unit, irradiance_to_kw_m2) * hours


def performance_ratio(e_ac_kwh, p_dc_kw, h_poa_kwh_m2, g_stc_kw_m2=1.0):
    """PR as a ratio, or NaN when any input makes it undefined (never 0 or inf)."""
    vals = (e_ac_kwh, p_dc_kw, h_poa_kwh_m2, g_stc_kw_m2)
    if any(v is None or not math.isfinite(float(v)) for v in vals):
        return float("nan")
    if p_dc_kw <= 0 or h_poa_kwh_m2 <= 0 or g_stc_kw_m2 <= 0:
        return float("nan")
    return (e_ac_kwh / p_dc_kw) / (h_poa_kwh_m2 / g_stc_kw_m2)


def as_percent(pr):
    """Display form only."""
    return float("nan") if pr is None or not math.isfinite(pr) else 100.0 * pr


def percentage_point_difference(pr, pr_ref):
    """100 * (PR - PR_ref), both stored as ratios."""
    if not (math.isfinite(pr) and math.isfinite(pr_ref)):
        return float("nan")
    return 100.0 * (pr - pr_ref)


def relative_change_percent(pr, pr_ref):
    """100 * (PR / PR_ref - 1); defined only for a finite, positive reference."""
    if not (math.isfinite(pr) and math.isfinite(pr_ref)) or pr_ref <= 0:
        return float("nan")
    return 100.0 * (pr / pr_ref - 1.0)


def resolve_status(insufficient_reasons, review_reasons):
    """Precedence INSUFFICIENT_DATA > REVIEW_REQUIRED > CHECKS_PASSED."""
    if insufficient_reasons:
        return INSUFFICIENT_DATA
    if review_reasons:
        return REVIEW_REQUIRED
    return CHECKS_PASSED


def _flag_tokens(text):
    return [t for t in str(text).split(";") if t and t != "nan"] if isinstance(text, str) else []


def daily_metrics(pairs, capacity_kw, cfg):
    """Aggregate a paired-interval table to one row per local reporting date."""
    elig = cfg["eligibility"]
    policy = cfg["flag_policy"]
    g = cfg["g_stc_kw_m2"]
    cap_ok = capacity_kw is not None and math.isfinite(float(capacity_kw)) and float(capacity_kw) > 0
    flag_cols = ["meter_flags", "poa0_flags", "poa1_flags", "poa2_flags"]
    reason_cols = sorted(r for r in pairs["exclusion_reason"].dropna().unique() if r != "OUTSIDE_WINDOW")
    rows = []
    for day, d in pairs.groupby("local_reporting_date", sort=True):
        w = d[d["in_window"]]
        p = w[w["paired"]]
        window_h, paired_h = float(w["interval_hours"].sum()), float(p["interval_hours"].sum())
        cs_w, cs_p = float(w["clearsky_kwh_m2"].sum()), float(p["clearsky_kwh_m2"].sum())
        e = float(p["e_ac_kwh"].sum()) if len(p) else float("nan")
        h = float(p["h_poa_kwh_m2"].sum()) if len(p) else float("nan")
        diag = performance_ratio(e, capacity_kw, h, g) if (len(p) and cap_ok) else float("nan")
        time_cov = paired_h / window_h if window_h > 0 else float("nan")
        cs_cov = cs_p / cs_w if cs_w > 0 else float("nan")

        withhold, context, review = 0, 0, set()
        for col in flag_cols:
            for text in w[col]:
                for tok in _flag_tokens(text):
                    action = policy.get(tok, "withhold")
                    if action == "context":
                        context += 1
                    else:
                        withhold += 1
                        review.add(REVIEW_REASON.get(tok, "UNCLASSIFIED_FLAG"))
        if w["negative_input"].any() and cfg["negative_inputs_in_window"] == "withhold":
            review.add("NEGATIVE_INPUT_IN_WINDOW")

        insufficient = []
        if not cap_ok:
            insufficient.append("INVALID_CAPACITY")
        if len(w) == 0:
            insufficient.append("NO_WINDOW_INTERVALS")
        elif len(p) == 0:
            insufficient.append("NO_PAIRED_DATA")
        else:
            if time_cov < elig["min_time_coverage"]:
                insufficient.append("LOW_TIME_COVERAGE")
            if not (cs_cov >= elig["min_clearsky_weighted_coverage"]):
                insufficient.append("LOW_CLEARSKY_WEIGHTED_COVERAGE")
            if not (h >= elig["min_paired_irradiation_kwh_m2"]):
                insufficient.append("LOW_IRRADIATION")
        if not math.isfinite(diag) and "INVALID_CAPACITY" not in insufficient and len(p):
            insufficient.append("PR_UNAVAILABLE")
        status = resolve_status(insufficient, review)

        row = {
            "local_reporting_date": day,
            "local_day_length_h": float(d["local_day_length_h"].iloc[0]),
            "window_intervals": len(w), "window_hours": window_h,
            "paired_intervals": len(p), "paired_hours": paired_h,
            "excluded_window_intervals": len(w) - len(p),
            "time_coverage": time_cov, "clearsky_weighted_coverage": cs_cov,
            "e_ac_kwh": e, "h_poa_kwh_m2": h, "capacity_kw": capacity_kw,
            "diagnostic_pr": diag,
            "reported_pr": diag if status == CHECKS_PASSED else float("nan"),
            "status": status,
            "reason_codes": ";".join(insufficient + sorted(review)),
            "n_withholding_flags": withhold, "n_context_flags": context,
            "n_paired_zero_output": int((p["meter_state"] == "OBSERVED_ZERO").sum()),
            "metric_version": cfg["metric_version"],
        }
        for r in reason_cols:
            row[f"n_excl_{r.lower()}"] = int((w["exclusion_reason"] == r).sum())
        rows.append(row)
    return pd.DataFrame(rows)
