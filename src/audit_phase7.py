"""Independent scoring audit of results/phase7/run1 (AI-assisted audit; no human review claimed).

Deliberately does NOT import src/evaluate.py. It re-derives completeness, categories, rates,
PR effects and unique counts from the saved detector outputs and injection truth with its own
simple logic and its own fault-specific reason table written from the protocol, then compares
with the saved scores. Writes results/phase7/analysis/scoring_audit.csv and audit_mismatches.csv.

Usage:
    python src/audit_phase7.py
"""
import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/phase7/run1"
OUT = ROOT / "results/phase7/analysis"

# From docs/experiment_protocol.md section 8 and the rule table (basic = shared + basic rules).
SPECIFIC = {("missing_daytime", "basic"): {"LOW_TIME_COVERAGE", "NO_PAIRED_DATA"},
            ("missing_daytime", "improved"): {"LOW_TIME_COVERAGE", "NO_PAIRED_DATA", "LOW_CLEARSKY_WEIGHTED_COVERAGE"},
            ("timestamp_shift", "basic"): set(), ("timestamp_shift", "improved"): {"TIMING_INCONSISTENT"},
            ("flatline_poa", "basic"): set(), ("flatline_poa", "improved"): {"POA_STALE"}}
SPECIFIC_CHECK = {("timestamp_shift", "improved"): "I3", ("flatline_poa", "improved"): "I2",
                  ("missing_daytime", "basic"): "B1", ("missing_daytime", "improved"): "I1"}
PLANNED = {"missing_daytime": 5, "timestamp_shift": 10, "flatline_poa": 6}


def split(x):
    return set(str(x).split(";")) - {"", "nan"} if isinstance(x, str) else set()


def my_category(applied, o_status, f_status, f_reasons, specific, spec_outcome):
    if not applied:
        return "UNSCORABLE"
    if o_status != "CHECKS_PASSED":
        return "ALREADY_WITHHELD"
    if f_status != "CHECKS_PASSED":
        return "DETECTED_SPECIFIC" if (f_reasons & specific) else "WITHHELD_OTHER"
    if specific and spec_outcome == "INCONCLUSIVE":
        return "INCONCLUSIVE"
    return "MISSED"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    truth = pd.read_csv(RUN / "injection_truth.csv")
    scores = pd.read_csv(RUN / "case_scores.csv")
    days = pd.read_csv(RUN / "detector_day_results.csv")
    checks = pd.read_parquet(RUN / "detector_check_results.parquet", columns=["run_key", "method", "check_id", "outcome"])
    unmod = pd.read_csv(RUN / "unmodified_counterparts.csv")
    sample = pd.read_csv(ROOT / "results/phase4/evaluation_window_sample.csv")
    meta = json.loads((RUN / "run_metadata.json").read_text(encoding="utf-8"))
    rows = []

    def rec(item, expected, observed, ok=None):
        rows.append({"item": item, "expected": expected, "observed": observed,
                     "result": "PASS" if (expected == observed if ok is None else ok) else "FAIL"})

    # completeness
    rec("COMPLETED marker present", True, (RUN / "COMPLETED").exists())
    rec("windows with an unmodified counterpart", 96, unmod["window_id"].nunique())
    rec("counterpart windows equal the frozen sample", True, set(unmod["window_id"]) == set(sample["window_id"]))
    planned = sum(PLANNED[f] for f in sample["family"])
    rec("planned cases (from frozen sample x case specs)", 672, planned)
    rec("truth records", planned, len(truth))
    rec("truth case ids unique", True, truth["case_id"].is_unique)
    per_window = truth.groupby("window_id").size()
    rec("every window has its family's case count", True,
        all(per_window[w] == PLANNED[f] for w, f in zip(sample["window_id"], sample["family"])))
    failures = (RUN / "failures.csv").exists()
    rec("failures file absent (no window raised)", False, failures)
    rec("scores rows = 2 methods x truth records", 2 * len(truth), len(scores))
    rec("each case scored once per method", True,
        scores.groupby(["case_id", "method"]).size().eq(1).all() and set(scores["case_id"]) == set(truth["case_id"]))
    applied = truth[truth["injection_status"] == "APPLIED"]
    rec("unscorable truth records", "see observed", int((truth["injection_status"] != "APPLIED").sum()), ok=True)
    keyed = set(days["run_key"])
    rec("every applied case and counterpart has detector output for both methods", True,
        set(applied["run_key"]) <= keyed and set(unmod["run_key"]) <= keyed
        and days.groupby("run_key")["method"].nunique().eq(2).all())
    rec("detector runs = counterparts + applied cases", meta["detector_runs"], len(unmod) + len(applied))
    rec("detector outputs carry no truth columns", True,
        not ({"case_id", "scenario", "severity", "family", "window_id"} & set(days.columns)))
    pop = pd.read_csv(RUN / "population_unmodified_days.csv")
    rec("population unique days per method", 1270, int(pop.groupby("method")["local_reporting_date"].nunique().min()))

    # independent re-scoring
    d = days.set_index(["run_key", "method"])
    c = checks.set_index(["run_key", "method", "check_id"])["outcome"]
    t = truth.set_index("case_id")
    mism = []
    my_rows = []
    for s in scores.itertuples():
        tr = t.loc[s.case_id]
        app = tr["injection_status"] == "APPLIED"
        o = d.loc[(tr["counterpart_run_key"], s.method)]
        f = d.loc[(tr["run_key"], s.method)] if app else None
        spec = SPECIFIC[(s.family, s.method)]
        ck = SPECIFIC_CHECK.get((s.family, s.method))
        spec_out = c.get((tr["run_key"], s.method, ck)) if (app and ck) else None
        cat = my_category(app, o["status"], f["status"] if app else None, split(f["reason_codes"]) if app else set(),
                          spec, spec_out)
        pp = 100 * (f["diagnostic_pr"] - o["diagnostic_pr"]) if app and math.isfinite(f["diagnostic_pr"]) \
            and math.isfinite(o["diagnostic_pr"]) else None
        my_rows.append({"case_id": s.case_id, "method": s.method, "my_category": cat, "my_pp": pp})
        if cat != s.category:
            mism.append({"case_id": s.case_id, "method": s.method, "saved": s.category, "audit": cat})
        if pp is not None and (s.pr_pp_difference is None or abs(pp - s.pr_pp_difference) > 1e-9):
            mism.append({"case_id": s.case_id, "method": s.method, "saved_pp": s.pr_pp_difference, "audit_pp": pp})
    mine = pd.DataFrame(my_rows)
    rec("category mismatches (independent re-scoring vs saved)", 0, sum("audit" in m for m in mism))
    rec("PR percentage-point mismatches", 0, sum("audit_pp" in m for m in mism))
    rec("category counts sum to scored cases", len(scores), int(scores["category"].value_counts().sum()))
    aw = scores[scores["category"] == "ALREADY_WITHHELD"]
    rec("no ALREADY_WITHHELD case counted as detection", True, not aw["category"].eq("DETECTED_SPECIFIC").any())
    un = scores.merge(truth[["case_id", "injection_status"]], on="case_id")
    rec("UNSCORABLE exactly where injection not applied", True,
        ((un["category"] == "UNSCORABLE") == (un["injection_status"] != "APPLIED")).all())

    # rates by method x family x scenario x severity
    rs = pd.read_csv(RUN / "rate_summary.csv")
    rs = rs[rs["grouping"] == "method+family+scenario+severity"]
    bad_rate = 0
    for (m, fam, scen, sev), g in mine.merge(scores[["case_id", "method", "family", "scenario", "severity",
                                                     "window_id"]], on=["case_id", "method"]).groupby(
            ["method", "family", "scenario", "severity"]):
        n = g["my_category"].value_counts()
        den = len(g) - n.get("UNSCORABLE", 0) - n.get("ALREADY_WITHHELD", 0)
        head = None if den == 0 else n.get("DETECTED_SPECIFIC", 0) / den
        cden = den - n.get("INCONCLUSIVE", 0)
        cons = None if cden == 0 else n.get("DETECTED_SPECIFIC", 0) / cden
        r = rs[(rs["method"] == m) & (rs["family"] == fam) & (rs["scenario"] == scen) & (rs["severity"] == sev)].iloc[0]
        same = lambda a, b: (a is None and pd.isna(b)) or (a is not None and not pd.isna(b) and abs(a - b) < 1e-12)  # noqa
        if not (r["headline_denominator"] == den and same(head, r["headline_detection_rate"])
                and r["conclusive_denominator"] == cden and same(cons, r["conclusive_only_detection_rate"])
                and r["unique_windows"] == g["window_id"].nunique()):
            bad_rate += 1
    rec("rate rows disagreeing with independent recomputation (headline, conclusive, denominators, unique windows)",
        0, bad_rate)
    rec("zero-denominator rate rows are null", True,
        rs.loc[rs["headline_denominator"] == 0, "headline_detection_rate"].isna().all())

    # alert burden (unique days)
    for name, frame in (("sampled", days[days["run_key"].isin(unmod["run_key"])]), ("population", pop)):
        u = frame.drop_duplicates(["method", "local_reporting_date"])
        saved = pd.read_csv(RUN / f"alert_burden_{name}.csv").set_index("method")
        for m, g in u.groupby("method"):
            rec(f"alert burden {name} {m}: unique days / not-passed",
                (int(saved.loc[m, "unique_days"]), int(saved.loc[m, "unconfirmed_alert_days"])),
                (len(g), int((g["status"] != "CHECKS_PASSED").sum())))

    audit = pd.DataFrame(rows)
    audit.to_csv(OUT / "scoring_audit.csv", index=False)
    pd.DataFrame(mism).to_csv(OUT / "audit_mismatches.csv", index=False)
    print(audit.to_string(index=False, max_colwidth=70))
    print("audit:", "PASS" if (audit["result"] == "PASS").all() else "FAIL")


if __name__ == "__main__":
    main()
