"""Pure scoring of injected cases against their unmodified counterparts (experiment-v3, A1 and A14).

Categories, in precedence order:
  UNSCORABLE > ALREADY_WITHHELD > DETECTED_SPECIFIC > WITHHELD_OTHER > INCONCLUSIVE > MISSED
Headline detection rate = DETECTED_SPECIFIC / (cases - UNSCORABLE - ALREADY_WITHHELD); INCONCLUSIVE stays
in the denominator. Rates with a zero denominator are None. Repeated severities on one window are
correlated sensitivity tests; unique windows are always reported.
"""
import math

import pandas as pd

CATEGORIES = ["UNSCORABLE", "ALREADY_WITHHELD", "DETECTED_SPECIFIC", "WITHHELD_OTHER", "INCONCLUSIVE", "MISSED"]
PASSED = "CHECKS_PASSED"


def specific_map(exp):
    """family -> method -> (specific reason codes, specific check ids the method actually runs)."""
    rules = pd.DataFrame(exp["rule_table"])
    out = {}
    for family, reasons in exp["scoring"]["fault_specific_reasons"].items():
        out[family] = {}
        for method in ("basic", "improved"):
            allowed = {"shared", "basic"} | ({"improved_only"} if method == "improved" else set())
            r = rules[rules["reason_code"].isin(reasons) & rules["class"].isin(allowed)]
            out[family][method] = (set(r["reason_code"]), set(r["id"]))
    return out


def codes(text):
    return set(c for c in str(text).split(";") if c and c != "nan") if isinstance(text, str) else set()


def pr_effect(pr_original, pr_faulted):
    """(percentage-point difference, relative change %, unavailable flag). PR stored as ratios."""
    fin = lambda x: x is not None and isinstance(x, (int, float)) and math.isfinite(x)  # noqa: E731
    if not (fin(pr_original) and fin(pr_faulted)):
        return None, None, True
    pp = 100.0 * (pr_faulted - pr_original)
    rel = 100.0 * (pr_faulted / pr_original - 1.0) if pr_original > 0 else None
    return pp, rel, False


def check_outcome(checks, ids):
    """Outcomes of the given check ids as 'ID:OUTCOME;...' (empty if the method has none)."""
    if checks is None or not ids:
        return ""
    c = checks[checks["check_id"].isin(ids)].sort_values("check_id")
    return ";".join(f"{a}:{b}" for a, b in zip(c["check_id"], c["outcome"]))


def score_case(method, family, injection_status, original_day, faulted_day, original_checks, faulted_checks,
               smap):
    """Score one case for one method. *_day are dicts with status, reason_codes, context_codes, diagnostic_pr."""
    reasons, ids = smap[family][method]
    o_status = original_day["status"]
    f_status = faulted_day["status"] if faulted_day is not None else None
    o_codes = codes(original_day["reason_codes"])
    f_codes = codes(faulted_day["reason_codes"]) if faulted_day is not None else set()
    f_spec_out = check_outcome(faulted_checks, ids)
    o_spec_out = check_outcome(original_checks, ids)
    spec_inconclusive = "INCONCLUSIVE" in f_spec_out
    new_specific = sorted((f_codes - o_codes) & reasons)

    if injection_status != "APPLIED":
        category = "UNSCORABLE"
    elif o_status != PASSED:
        category = "ALREADY_WITHHELD"
    elif f_status != PASSED and (f_codes & reasons):
        category = "DETECTED_SPECIFIC"
    elif f_status != PASSED:
        category = "WITHHELD_OTHER"
    elif ids and spec_inconclusive:
        category = "INCONCLUSIVE"
    else:
        category = "MISSED"

    timing_o = check_outcome(original_checks, {"I3"}) if method == "improved" else ""
    timing_f = check_outcome(faulted_checks, {"I3"}) if method == "improved" else ""
    pp, rel, unavailable = pr_effect(original_day.get("diagnostic_pr"),
                                     faulted_day.get("diagnostic_pr") if faulted_day is not None else None)
    return {"method": method, "category": category,
            "original_status": o_status, "faulted_status": f_status,
            "original_reasons": ";".join(sorted(o_codes)), "faulted_reasons": ";".join(sorted(f_codes)),
            "new_reasons": ";".join(sorted(f_codes - o_codes)), "new_specific_reasons": ";".join(new_specific),
            "specific_check_outcome_original": o_spec_out, "specific_check_outcome_faulted": f_spec_out,
            "timing_outcome_original": timing_o, "timing_outcome_faulted": timing_f,
            "timing_applicable": (timing_f.startswith("I3:") and not timing_f.endswith("INCONCLUSIVE"))
            if timing_f else None,
            "faulted_context": faulted_day.get("context_codes", "") if faulted_day is not None else "",
            "reporting_withheld": (f_status != PASSED) if f_status is not None else None,
            "diagnostic_pr_original": original_day.get("diagnostic_pr"),
            "diagnostic_pr_faulted": faulted_day.get("diagnostic_pr") if faulted_day is not None else None,
            "pr_pp_difference": pp, "pr_relative_change_pct": rel, "pr_unavailable": unavailable}


def rate(num, den):
    return None if den == 0 else num / den


def summarize(scores, by):
    """Counts and rates per group. scores needs: category, window_id, timing_applicable, pr_unavailable,
    pr_pp_difference, plus the grouping columns."""
    rows = []
    for key, g in scores.groupby(by, dropna=False, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        n = {c: int((g["category"] == c).sum()) for c in CATEGORIES}
        scorable = len(g) - n["UNSCORABLE"] - n["ALREADY_WITHHELD"]
        conclusive_den = scorable - n["INCONCLUSIVE"]
        sc = g[~g["category"].isin(["UNSCORABLE", "ALREADY_WITHHELD"])]
        timing = sc["timing_applicable"].dropna()
        pp = sc["pr_pp_difference"].dropna()
        row = dict(zip(by, key))
        row.update({"cases": len(g), "unique_windows": g["window_id"].nunique(), **{c.lower(): n[c] for c in CATEGORIES},
                    "headline_denominator": scorable,
                    "headline_detection_rate": rate(n["DETECTED_SPECIFIC"], scorable),
                    "conclusive_denominator": conclusive_den,
                    "conclusive_only_detection_rate": rate(n["DETECTED_SPECIFIC"], conclusive_den),
                    "withheld_numerator": n["DETECTED_SPECIFIC"] + n["WITHHELD_OTHER"],
                    "withheld_rate": rate(n["DETECTED_SPECIFIC"] + n["WITHHELD_OTHER"], scorable),
                    "timing_applicable_numerator": int(timing.sum()) if len(timing) else None,
                    "timing_applicable_denominator": len(timing) if len(timing) else None,
                    "timing_applicability": rate(int(timing.sum()), len(timing)) if len(timing) else None,
                    "pr_unavailable": int(sc["pr_unavailable"].sum()),
                    "pr_pp_median": float(pp.median()) if len(pp) else None,
                    "pr_pp_min": float(pp.min()) if len(pp) else None,
                    "pr_pp_max": float(pp.max()) if len(pp) else None})
        rows.append(row)
    return pd.DataFrame(rows)


def alert_burden(unmodified_days, label):
    """Share of UNIQUE unmodified days not CHECKS_PASSED per method (unconfirmed alerts)."""
    u = unmodified_days.drop_duplicates(["method", "local_reporting_date"])
    rows = []
    for method, g in u.groupby("method"):
        n = len(g)
        not_passed = int((g["status"] != PASSED).sum())
        rows.append({"population": label, "method": method, "unique_days": n,
                     "unconfirmed_alert_days": not_passed, "unconfirmed_alert_share": rate(not_passed, n),
                     "reporting_coverage_retained": rate(n - not_passed, n),
                     "review_days": int((g["status"] == "REVIEW_REQUIRED").sum()),
                     "insufficient_days": int((g["status"] == "INSUFFICIENT_DATA").sum())})
    return pd.DataFrame(rows)
