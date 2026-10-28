"""Basic and improved validation methods (experiment-v2): evaluate days and aggregate statuses.

evaluate(obs, site, meta, metric_cfg, exp_cfg, timing_baseline, dates) receives only the
detector-input observations (evidence.DETECTOR_INPUT_COLUMNS), site metadata, measurement
metadata, the frozen configurations and the development timing baseline. It never
receives injection labels, scenario names, severities, expected outcomes or the
evaluation-window list.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks_basic as B  # noqa: E402
import checks_improved as I  # noqa: E402
import evidence as E  # noqa: E402
import metrics as M  # noqa: E402

RANK = {M.CHECKS_PASSED: 0, M.REVIEW_REQUIRED: 1, M.INSUFFICIENT_DATA: 2}
LIMITATION_BASIC = "basic method: timing, stale values and midday completeness not checked"
LIMITATION_TIMING = "timing not verified (TIMING_INCONCLUSIVE)"


def aggregate(day_checks):
    """Frozen precedence INSUFFICIENT_DATA > REVIEW_REQUIRED > CHECKS_PASSED from check results."""
    flagged = day_checks[day_checks["outcome"] == B.FLAGGED]
    ins = list(dict.fromkeys(flagged.loc[flagged["action"] == M.INSUFFICIENT_DATA, "reason_code"]))
    rev = sorted(set(flagged.loc[flagged["action"] == M.REVIEW_REQUIRED, "reason_code"]))
    ctx = sorted(set(day_checks.loc[day_checks["action"] == "CONTEXT", "reason_code"]))
    return M.resolve_status(ins, rev), ins, rev, ctx


def evaluate(obs, site, meta, metric_cfg, exp_cfg, timing_baseline, dates, methods=("basic", "improved")):
    ev = E.build_evidence(obs, site, meta, metric_cfg, dates)
    dm_all = E.shared_day_metrics(ev["pairs"], site["dc_capacity_kw"], metric_cfg["g_stc_kw_m2"]).set_index(
        "local_reporting_date")
    csm = None
    if "improved" in methods:
        t0 = ev["meter"]["interval_start_utc"].min() - pd.Timedelta("3h")
        t1 = ev["meter"]["interval_end_utc"].max() + pd.Timedelta("3h")
        csm = I.ClearSkyMinutes(site, t0, t1)
    pairs_by = dict(tuple(ev["pairs"].groupby("local_reporting_date")))
    meter_by = dict(tuple(ev["meter"].groupby("local_reporting_date")))
    poa_by = dict(tuple(ev["poa"].groupby("local_reporting_date")))
    off_by = {s: dict(tuple(o.groupby("local_reporting_date"))) for s, o in ev["off_grid"].items()}
    empty_off = pd.DataFrame(columns=["interval_start_utc", "in_window"])

    checks, days = [], []
    for day in sorted(set(dates)):
        dm = dm_all.loc[day]
        ev_day = {"pairs": pairs_by[day], "meter": meter_by[day], "poa": poa_by[day],
                  "off_grid": {s: off_by[s].get(day, empty_off) for s in off_by}}
        common = B.shared_checks(day, dm, bool(dm["capacity_ok"])) + B.basic_checks(day, ev_day, dm, exp_cfg, meta)
        extra = []
        if "improved" in methods:
            extra = [I.midday_completeness(day, dm, exp_cfg), I.poa_stale(day, ev_day["poa"], exp_cfg),
                     I.timing(day, ev_day["poa"], csm, exp_cfg, timing_baseline)]
        ranks = {}
        for method in methods:
            rows = common + (extra if method == "improved" else [])
            dc = pd.DataFrame(rows)
            dc.insert(1, "method", method)
            status, ins, rev, ctx = aggregate(dc)
            ranks[method] = RANK[status]
            lim = [LIMITATION_BASIC] if method == "basic" else []
            if method == "improved" and "TIMING_INCONCLUSIVE" in ctx and status == M.CHECKS_PASSED:
                lim.append(LIMITATION_TIMING)
            t = next((r for r in extra if r["check_id"] == "I3"), None) if method == "improved" else None
            days.append({
                "local_reporting_date": day, "method": method, "status": status,
                "reason_codes": ";".join(ins + rev), "context_codes": ";".join(ctx), "limitations": "; ".join(lim),
                "diagnostic_pr": dm["diagnostic_pr"],
                "reported_pr": dm["diagnostic_pr"] if status == M.CHECKS_PASSED else float("nan"),
                "window_intervals": int(dm["window_intervals"]), "paired_intervals": int(dm["paired_intervals"]),
                "time_coverage": dm["time_coverage"], "clearsky_weighted_coverage": dm["clearsky_weighted_coverage"],
                "e_ac_kwh": dm["e_ac_kwh"], "h_poa_kwh_m2": dm["h_poa_kwh_m2"],
                "timing_outcome": t["outcome"] if t else "", "timing_best_lag_min": t["measured"] if t else None,
                "experiment_version": exp_cfg["experiment_version"]})
            checks.append(dc)
        if "basic" in ranks and "improved" in ranks and ranks["improved"] < ranks["basic"]:
            raise AssertionError(f"{day}: improved reported a better status than basic")
    return pd.concat(checks, ignore_index=True), pd.DataFrame(days), ev
