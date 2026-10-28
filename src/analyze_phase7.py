"""Read-only analysis of results/phase7/run1 (never modifies run1). Writes results/phase7/analysis/.

Tables:
  primary_results.csv         family x severity x method: detected/denominator and every category
  secondary_results.csv       secondary scenarios, same layout
  common_subset_results.csv   SUPPLEMENTARY: cases whose unmodified counterpart passes BOTH methods
  timing_applicability_by_season.csv
  pr_effect_summary.csv       pp change by method x family x scenario x category; reported-despite-change counts
  reporting_coverage.csv      sampled unique days and full eligible population
  case_reconciliation.csv     planned / attempted / applied / unscorable / failed per family and scenario
  representative_cases.csv    rule-based examples with detector evidence

Usage:
    python src/analyze_phase7.py
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/phase7/run1"
OUT = ROOT / "results/phase7/analysis"
CATS = ["DETECTED_SPECIFIC", "WITHHELD_OTHER", "INCONCLUSIVE", "MISSED", "ALREADY_WITHHELD", "UNSCORABLE"]


def wide(scores, keys):
    rows = []
    for k, g in scores.groupby(keys + ["method"], sort=True):
        k = dict(zip(keys + ["method"], k))
        n = g["category"].value_counts()
        den = len(g) - n.get("UNSCORABLE", 0) - n.get("ALREADY_WITHHELD", 0)
        det = int(n.get("DETECTED_SPECIFIC", 0))
        k.update({"cases": len(g), "unique_windows": g["window_id"].nunique(), "detected": det, "denominator": den,
                  "detected_of_denominator": f"{det}/{den}",
                  "headline_rate": None if den == 0 else round(det / den, 3),
                  "conclusive_only_rate (secondary)": None if den - n.get("INCONCLUSIVE", 0) == 0
                  else round(det / (den - n.get("INCONCLUSIVE", 0)), 3),
                  **{c.lower(): int(n.get(c, 0)) for c in CATS[1:]}})
        rows.append(k)
    return pd.DataFrame(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    scores = pd.read_csv(RUN / "case_scores.csv")
    truth = pd.read_csv(RUN / "injection_truth.csv")
    days = pd.read_csv(RUN / "detector_day_results.csv")
    checks = pd.read_parquet(RUN / "detector_check_results.parquet")
    unmod = pd.read_csv(RUN / "unmodified_counterparts.csv")
    sample = pd.read_csv(ROOT / "results/phase4/evaluation_window_sample.csv")
    scores["scenario_class"] = scores["scenario"].str.split(":").str[0]

    prim, sec = scores[scores["scenario_class"] == "primary"], scores[scores["scenario_class"] == "secondary"]
    wide(prim, ["family", "scenario", "severity"]).to_csv(OUT / "primary_results.csv", index=False)
    wide(sec, ["family", "scenario", "severity"]).to_csv(OUT / "secondary_results.csv", index=False)

    piv = scores.pivot_table(index="case_id", columns="method", values="original_status", aggfunc="first")
    both_pass = set(piv.index[(piv["basic"] == "CHECKS_PASSED") & (piv["improved"] == "CHECKS_PASSED")])
    cs = wide(prim[prim["case_id"].isin(both_pass)], ["family", "severity"])
    cs.insert(0, "view", "SUPPLEMENTARY common subset: counterpart passes both methods")
    cs.to_csv(OUT / "common_subset_results.csv", index=False)

    ts = scores[(scores["family"] == "timestamp_shift") & (scores["method"] == "improved")
                & ~scores["category"].isin(["UNSCORABLE", "ALREADY_WITHHELD"])]
    ta = ts.groupby(["scenario", "season"]).agg(cases=("case_id", "size"), unique_windows=("window_id", "nunique"),
                                               timing_conclusive=("timing_applicable", lambda x: int((x == True).sum())),  # noqa: E712
                                               inconclusive_category=("category", lambda x: int((x == "INCONCLUSIVE").sum())))
    ta["timing_applicability"] = (ta["timing_conclusive"] / ta["cases"]).round(3)
    ta.reset_index().to_csv(OUT / "timing_applicability_by_season.csv", index=False)

    sc = scores[~scores["category"].isin(["UNSCORABLE", "ALREADY_WITHHELD"])].copy()
    sc["abs_pp"] = sc["pr_pp_difference"].abs()
    pe = sc.groupby(["method", "family", "scenario_class", "category"]).agg(
        cases=("case_id", "size"), pr_unavailable=("pr_unavailable", "sum"),
        median_pp=("pr_pp_difference", "median"), min_pp=("pr_pp_difference", "min"),
        max_pp=("pr_pp_difference", "max"), median_abs_pp=("abs_pp", "median"), max_abs_pp=("abs_pp", "max"),
        median_rel_pct=("pr_relative_change_pct", "median")).reset_index()
    pe.to_csv(OUT / "pr_effect_summary.csv", index=False)
    rep = sc[sc["faulted_status"] == "CHECKS_PASSED"]
    reported = rep.groupby(["method", "family", "scenario_class"]).agg(
        faulted_cases_still_reported=("case_id", "size"),
        abs_pp_ge_0_5=("abs_pp", lambda x: int((x >= 0.5).sum())),
        abs_pp_ge_1=("abs_pp", lambda x: int((x >= 1).sum())),
        abs_pp_ge_5=("abs_pp", lambda x: int((x >= 5).sum())),
        max_abs_pp=("abs_pp", "max")).reset_index()
    reported.to_csv(OUT / "reported_despite_pr_change.csv", index=False)

    cov = pd.concat([pd.read_csv(RUN / "alert_burden_sampled.csv"), pd.read_csv(RUN / "alert_burden_population.csv")])
    cov.to_csv(OUT / "reporting_coverage.csv", index=False)

    planned = {"missing_daytime": {"primary:rows_removed": 4, "secondary:explicit_nulls": 1},
               "timestamp_shift": {"primary:poa_only": 6, "secondary:whole_file": 4},
               "flatline_poa": {"primary:hold_last": 5, "secondary:fixed_value_600": 1}}
    failed = pd.read_csv(RUN / "failures.csv") if (RUN / "failures.csv").exists() else pd.DataFrame(columns=["window_id"])
    rec = []
    for fam, scen_map in planned.items():
        nwin = int((sample["family"] == fam).sum())
        for scen, k in scen_map.items():
            t = truth[(truth["family"] == fam) & (truth["scenario"] == scen)]
            rec.append({"family": fam, "scenario": scen, "windows": nwin, "planned": nwin * k, "attempted": len(t),
                        "applied": int((t["injection_status"] == "APPLIED").sum()),
                        "unscorable": int((t["injection_status"] != "APPLIED").sum()),
                        "unscorable_reasons": t.loc[t["injection_status"] != "APPLIED", "unscorable_reason"]
                        .value_counts().to_dict(),
                        "failed_windows": int(failed["window_id"].isin(sample.loc[sample["family"] == fam,
                                                                                   "window_id"]).sum()),
                        "scored_rows": int(scores["case_id"].isin(t["case_id"]).sum())})
    pd.DataFrame(rec).to_csv(OUT / "case_reconciliation.csv", index=False)

    # representative cases (stated selection rules)
    def evidence(run_key, method, cid):
        e = checks[(checks["run_key"] == run_key) & (checks["method"] == method) & (checks["check_id"] == cid)]
        return e["evidence"].iloc[0] if len(e) else ""

    t = truth.set_index("case_id")
    picks = []

    def add(rule, frame, sort_col=None, asc=False):
        if frame.empty:
            picks.append({"rule": rule, "case_id": "(no case in this category)"})
            return
        r = frame.sort_values([sort_col, "case_id"] if sort_col else ["case_id"],
                              ascending=[asc, True] if sort_col else [True]).iloc[0]
        tr = t.loc[r["case_id"]]
        cid = {"timestamp_shift": "I3", "flatline_poa": "I2", "missing_daytime": "I1"}[r["family"]]
        picks.append({"rule": rule, "case_id": r["case_id"], "method": r["method"], "date": r["local_reporting_date"],
                      "category": r["category"], "original_status": r["original_status"],
                      "faulted_status": r["faulted_status"], "faulted_reasons": r["faulted_reasons"],
                      "faulted_context": r["faulted_context"], "pr_pp_difference": r["pr_pp_difference"],
                      "diagnostic_pr_original": r["diagnostic_pr_original"],
                      "diagnostic_pr_faulted": r["diagnostic_pr_faulted"],
                      "injection": f"{tr['n_rows_affected']} rows, {tr['n_values_changed']} values changed, "
                                   f"{tr['n_collisions']} collisions, placement {tr['placement']}",
                      f"{cid}_evidence_faulted": evidence(tr["run_key"], "improved", cid)})

    imp, bas = scores[scores["method"] == "improved"], scores[scores["method"] == "basic"]
    add("largest |PR change| among MISSED cases (improved, primary)",
        imp[(imp["category"] == "MISSED") & (imp["scenario_class"] == "primary")].assign(
            a=lambda x: x["pr_pp_difference"].abs()).dropna(subset=["a"]), "a")
    both = imp.merge(bas[["case_id", "category"]], on="case_id", suffixes=("", "_basic"))
    add("caught by improved, missed by basic (largest |PR change|)",
        both[(both["category"] == "DETECTED_SPECIFIC") & (both["category_basic"] == "MISSED")].assign(
            a=lambda x: x["pr_pp_difference"].abs()).dropna(subset=["a"]), "a")
    add("timing INCONCLUSIVE shift case (largest |PR change|)",
        imp[(imp["category"] == "INCONCLUSIVE")].assign(a=lambda x: x["pr_pp_difference"].abs()).dropna(subset=["a"]),
        "a")
    add("already withheld before injection (first by case id, improved)", imp[imp["category"] == "ALREADY_WITHHELD"])
    add("short flatline below threshold: 15-min hold-last MISSED (largest |PR change|)",
        imp[(imp["family"] == "flatline_poa") & (imp["severity"] == 15) & (imp["category"] == "MISSED")].assign(
            a=lambda x: x["pr_pp_difference"].abs()).dropna(subset=["a"]), "a")
    add("withheld through other reasons (improved, first by case id)", imp[imp["category"] == "WITHHELD_OTHER"])
    pd.DataFrame(picks).to_csv(OUT / "representative_cases.csv", index=False)

    print(wide(prim, ["family", "severity"])[["family", "severity", "method", "detected_of_denominator",
                                               "inconclusive", "missed", "withheld_other", "already_withheld",
                                               "unscorable", "unique_windows"]].to_string(index=False))


if __name__ == "__main__":
    main()
