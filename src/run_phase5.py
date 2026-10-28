"""Phase 5: run the tested detectors on DEVELOPMENT dates only and compare with the Phase 4 prototype.

Writes results/phase5/: development_method_results.csv, development_check_results.parquet,
development_check_outcome_counts.csv, prototype_comparison.csv, shared_metric_comparison.csv.

Usage:
    python src/run_phase5.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect as D  # noqa: E402
import detector_inputs as DI  # noqa: E402
import evidence as E  # noqa: E402
import ingest  # noqa: E402

ROOT = ingest.ROOT
OUT = ROOT / "results" / "phase5"


def verify_spec():
    m = json.loads((ROOT / "results/phase4/specification_manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in m["file_sha256"].items() if ingest.sha256_file(ROOT / f) != h]
    if bad:
        raise SystemExit(f"frozen specification files changed: {bad}")
    return m


def codes(s):
    return set(c for c in str(s).split(";") if c and c != "nan")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    verify_spec()
    exp = yaml.safe_load((ROOT / "config/experiment_v2.yaml").read_text(encoding="utf-8"))
    mcfg = yaml.safe_load((ROOT / "config/metric_definition.yaml").read_text(encoding="utf-8"))
    proc = ROOT / "data/processed"
    site = pd.read_parquet(proc / "site_metadata.parquet").iloc[0].to_dict()
    meta = E.measurement_meta(ingest.load_config())
    summ = json.loads((ROOT / "results/phase4/development_analysis_summary.json").read_text(encoding="utf-8"))
    baseline = summ["timing_development"]["baseline_median_lag_min"]
    if baseline != exp["methods"]["improved"]["timing"]["baseline_lag_min"]:
        raise SystemExit("timing baseline differs from configuration")
    a, b = exp["periods"]["development"]["start_local_date"], exp["periods"]["development"]["end_local_date"]
    dates = [d.strftime("%Y-%m-%d") for d in pd.date_range(a, pd.Timestamp(b) - pd.Timedelta(days=1))]
    obs, _lineage = DI.load_detector_input(proc, a, b, site["local_timezone"])
    checks, days, _ = D.evaluate(obs, site, meta, mcfg, exp, baseline, dates)

    days.to_csv(OUT / "development_method_results.csv", index=False)
    checks.astype({"measured": str, "threshold": str}).to_parquet(OUT / "development_check_results.parquet",
                                                                  index=False)
    (checks.groupby(["method", "check_id", "outcome"]).size().rename("days").reset_index()
     .to_csv(OUT / "development_check_outcome_counts.csv", index=False))

    proto = pd.read_csv(ROOT / "results/phase4/dev_method_status_prototype.csv")
    ptim = pd.read_csv(ROOT / "results/phase4/dev_timing_lags.csv").set_index("local_reporting_date")
    impl_t = checks[checks["check_id"] == "I3"].set_index("local_reporting_date")
    rows = []
    for _, r in days.iterrows():
        p = proto[(proto["local_reporting_date"] == r["local_reporting_date"]) & (proto["method"] == r["method"])].iloc[0]
        row = {"local_reporting_date": r["local_reporting_date"], "method": r["method"],
               "prototype_status": p["status"], "implementation_status": r["status"],
               "status_match": p["status"] == r["status"],
               "prototype_reasons": p["reason_codes"], "implementation_reasons": r["reason_codes"],
               "reasons_match": codes(p["reason_codes"]) == codes(r["reason_codes"]),
               "prototype_context": p["context_codes"], "implementation_context": r["context_codes"],
               "context_match": codes(p["context_codes"]) == codes(r["context_codes"])}
        if r["method"] == "improved":
            d = r["local_reporting_date"]
            pt = ptim.loc[d] if d in ptim.index else None
            it = impl_t.loc[d]
            ie = json.loads(it["evidence"])
            row.update(prototype_best_lag=None if pt is None else pt["best_lag_min"],
                       implementation_best_lag=ie.get("best_lag_min"),
                       prototype_conclusive=None if pt is None else bool(pt["conclusive"]),
                       implementation_conclusive=it["outcome"] != "INCONCLUSIVE",
                       implementation_inconclusive_reason=ie.get("inconclusive_reason", ""))
            row["timing_conclusive_match"] = row["prototype_conclusive"] == row["implementation_conclusive"] if \
                pt is not None else (not row["implementation_conclusive"])
            pl, il = row["prototype_best_lag"], row["implementation_best_lag"]
            row["timing_lag_match"] = (pd.isna(pl) and il is None) or (il is not None and not pd.isna(pl)
                                                                        and int(pl) == int(il))
        rows.append(row)
    comp = pd.DataFrame(rows)
    comp.to_csv(OUT / "prototype_comparison.csv", index=False)

    ph3 = pd.read_csv(ROOT / "results/phase3/daily_pr_dev.csv").set_index("local_reporting_date")
    bd = days[days["method"] == "basic"].set_index("local_reporting_date")
    sm = pd.DataFrame({
        "window_intervals_diff": bd["window_intervals"] - ph3["window_intervals"],
        "paired_intervals_diff": bd["paired_intervals"] - ph3["paired_intervals"],
        "e_ac_kwh_absdiff": (bd["e_ac_kwh"] - ph3["e_ac_kwh"]).abs(),
        "h_poa_absdiff": (bd["h_poa_kwh_m2"] - ph3["h_poa_kwh_m2"]).abs(),
        "diagnostic_pr_absdiff": (bd["diagnostic_pr"] - ph3["diagnostic_pr"]).abs(),
        "time_coverage_absdiff": (bd["time_coverage"] - ph3["time_coverage"]).abs(),
        "cs_coverage_absdiff": (bd["clearsky_weighted_coverage"] - ph3["clearsky_weighted_coverage"]).abs()})
    sm.to_csv(OUT / "shared_metric_comparison.csv")

    for m in ("basic", "improved"):
        c = comp[comp["method"] == m]
        print(m, days[days["method"] == m]["status"].value_counts().to_dict(),
              "| status mismatches:", int((~c["status_match"]).sum()),
              "reason mismatches:", int((~c["reasons_match"]).sum()),
              "context mismatches:", int((~c["context_match"]).sum()))
    ci = comp[comp["method"] == "improved"].astype({"implementation_conclusive": bool, "timing_conclusive_match": bool,
                                                     "timing_lag_match": bool})
    print("timing inconclusive days:", int((~ci["implementation_conclusive"]).sum()),
          "| conclusive mismatches:", int((~ci["timing_conclusive_match"]).sum()),
          "lag mismatches:", int((~ci["timing_lag_match"]).sum()))
    print("shared metrics max abs diff:", sm.abs().max().to_dict())


if __name__ == "__main__":
    main()
