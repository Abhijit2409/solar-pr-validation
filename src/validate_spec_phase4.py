"""Phase 4 specification consistency checks and specification manifest (freeze A).

Writes results/phase4/specification_checks.csv and results/phase4/specification_manifest.json.
Exit 0 only if every check passes.

Usage:
    python src/validate_spec_phase4.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402
import phase4_development_analysis as A  # noqa: E402

ROOT = ingest.ROOT
OUT = ROOT / "results" / "phase4"


def main():
    exp = yaml.safe_load((ROOT / "config/experiment.yaml").read_text(encoding="utf-8"))
    met = yaml.safe_load((ROOT / "config/metric_definition.yaml").read_text(encoding="utf-8"))
    dcfg = yaml.safe_load((ROOT / "config/data_structure.yaml").read_text(encoding="utf-8"))
    rows = []

    def check(cid, name, ok, measured):
        rows.append({"check_id": cid, "check": name, "result": "PASS" if ok else "FAIL", "measured": str(measured)})

    dev, ev = exp["periods"]["development"], exp["periods"]["evaluation"]
    check("C01", "Periods contiguous, non-overlapping, half-open, inside v1 scope",
          dev["start_local_date"] < dev["end_local_date"] == ev["start_local_date"] < ev["end_local_date"]
          and dev["start_local_date"] == met["development_period"]["start_local_date"]
          and dev["end_local_date"] == met["development_period"]["end_local_date"]
          and ev["end_local_date"] <= dcfg["scope"]["end_local"][:10],
          f"dev [{dev['start_local_date']}, {dev['end_local_date']}) eval [{ev['start_local_date']}, {ev['end_local_date']})")

    sh = exp["shared"]
    check("C02", "Shared rules equal the Phase 3 metric configuration",
          sh["capacity_kw"] == met["capacity"]["expected_kw"]
          and sh["reporting_window_min_solar_elevation_deg"] == met["reporting_window"]["min_solar_elevation_deg"]
          and sh["min_paired_irradiation_kwh_m2"] == met["eligibility"]["min_paired_irradiation_kwh_m2"]
          and sh["status_precedence"] == met["status_precedence"] and sh["metric_version"] == met["metric_version"],
          "capacity, window, min irradiation, precedence, version")

    b, imp = exp["methods"]["basic"], exp["methods"]["improved"]
    z = b["zero_output_policy"]
    ok3 = (0 < b["min_time_coverage"] <= 1 and 0 < imp["midday_completeness"]["min_clearsky_weighted_coverage"] <= 1
           and 0 < z["material_zero_share_of_h"] < 1 and z["suspicious_meter_zero_min_poa_wm2"] > 0
           and 0 <= imp["poa_stale"]["tolerance_wm2"] < 0.1 / 2 + 1e-12 and imp["poa_stale"]["min_run_values"] >= 2
           and imp["timing"]["threshold_min"] > 0 and imp["timing"]["search_range_min"] >= 60 + imp["timing"]["threshold_min"]
           + abs(imp["timing"]["baseline_lag_min"]) - 20)
    check("C03", "Thresholds in valid ranges (stale tolerance below half the 0.1 W/m2 resolution; lag search covers "
                 "the largest shift)", ok3, "see config")

    summ = json.loads((OUT / "development_analysis_summary.json").read_text(encoding="utf-8"))
    td = summ["timing_development"]
    check("C04", "Timing baseline equals the development median; threshold flags 0 conclusive development days",
          imp["timing"]["baseline_lag_min"] == td["baseline_median_lag_min"]
          and td["flagged_at_threshold"][str(imp["timing"]["threshold_min"])] == 0,
          f"baseline {td['baseline_median_lag_min']} on {td['conclusive_days']} conclusive days")

    f = exp["faults"]
    ok5 = (all(d % 15 == 0 for d in f["missing_daytime"]["primary"]["durations_min"])
           and all(d % 5 == 0 for d in f["flatline_poa"]["primary"]["durations_min"])
           and all(s % 5 == 0 and s != 0 for s in f["timestamp_shift"]["primary"]["shifts_min"])
           and set(map(abs, f["timestamp_shift"]["primary"]["shifts_min"])) == {15, 30, 60}
           and len({f[k]["primary"]["seed"] for k in f if k != "unscorable_if"} | {exp["window_selection"]["sampling_seed"]}) == 4)
    check("C05", "Fault severities on the sensor grids; shifts +-15/30/60; seeds distinct", ok5,
          "missing 15-min multiples; flatline 5-min multiples")

    elig = pd.read_csv(OUT / "evaluation_structural_eligibility.csv")
    sample = pd.read_csv(OUT / "evaluation_window_sample.csv")
    ws = exp["window_selection"]
    eligible = set(elig.loc[elig["eligible"], "local_reporting_date"])
    redraw = A.draw_sample(elig, exp)
    ok6 = (sample["local_reporting_date"].is_unique and set(sample["local_reporting_date"]) <= eligible
           and sample["local_reporting_date"].between(ev["start_local_date"], ev["end_local_date"], inclusive="left").all()
           and (sample.groupby(["family", "season"]).size() == ws["windows_per_family_per_season"]).all()
           and redraw.equals(sample))
    check("C06", "Window sample: eligible, in evaluation period, disjoint, 8 per family-season, reproducible from seed",
          ok6, f"{sample['local_reporting_date'].nunique()} unique windows")

    rules = pd.DataFrame(exp["rule_table"])
    produced = set(rules.loc[rules["status"] != "context", "reason_code"])
    ref = {r for v in exp["scoring"]["fault_specific_reasons"].values() for r in v}
    ok7 = (ref <= produced and rules["id"].is_unique and rules["reason_code"].is_unique
           and set(rules["class"]) <= {"shared", "basic", "improved_only"}
           and set(rules["chosen"]) <= {"before", "after_development"})
    check("C07", "Rule table well formed; every fault-specific reason is produced by a non-context rule", ok7,
          f"{len(rules)} rules; scored reasons {sorted(ref)}")

    p3 = json.loads((ROOT / "results/phase3/phase3_manifest.json").read_text(encoding="utf-8"))
    check("C08", "Phase 3 configuration preserved unchanged (phase3-v1 hash)",
          ingest.sha256_file(ROOT / "config/metric_definition.yaml") == p3["config_sha256"]["config/metric_definition.yaml"],
          "metric_definition.yaml unchanged")

    dev_files = ["dev_timing_lags.csv", "dev_zero_pr_effect_passed_days.csv", "dev_method_status_prototype.csv"]
    in_dev = all(pd.read_csv(OUT / fn)["local_reporting_date"].between(dev["start_local_date"], dev["end_local_date"],
                                                                        inclusive="left").all() for fn in dev_files)
    eval_cols = set(elig.columns)
    value_free = not ({"diagnostic_pr", "e_ac_kwh", "h_poa_kwh_m2", "status", "value"} & eval_cols)
    check("C09", "Value-based Phase 4 outputs contain only development dates; evaluation outputs are structural only",
          in_dev and value_free, f"eligibility columns: {sorted(eval_cols)}")

    src = json.loads((ROOT / "docs/source_manifest.json").read_text(encoding="utf-8"))
    raw_ok = all(ingest.sha256_file(ROOT / x["local_path"]) == x["sha256"] for x in src["files"])
    proc_ok = all(ingest.sha256_file(ROOT / "data/processed" / f"{t}.parquet") == h
                  for t, h in p3["inputs"]["processed_file_sha256"].items())
    check("C10", "Raw files and Phase 2 processed tables unchanged", raw_ok and proc_ok,
          f"raw {len(src['files'])}, processed 5")

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "specification_checks.csv", index=False)
    files = ["config/experiment.yaml", "docs/experiment_protocol.md", "config/metric_definition.yaml",
             "config/data_structure.yaml", "docs/metric_definition.md", "docs/source_manifest.json",
             "results/phase2/run_manifest.json", "results/phase2/validation_manifest.json",
             "results/phase3/phase3_manifest.json", "results/phase3/phase3_validation_manifest.json",
             "results/phase3/daily_pr_dev.csv", "results/phase3/paired_intervals_dev.parquet",
             "src/phase4_development_analysis.py", "src/validate_spec_phase4.py"] + \
        sorted(f"results/phase4/{p.name}" for p in OUT.glob("*") if p.suffix in (".csv", ".json")
               and p.name not in ("specification_manifest.json", "specification_checks.csv"))
    manifest = {
        "checkpoint": "A - specification freeze (end of Phase 4)",
        "experiment_version": exp["experiment_version"],
        "created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "frozen_now": ["data and date boundaries", "metric definition (phase3-v1 shared rules)",
                       "basic and improved methods and thresholds", "fault specification", "seeds and sampling policy",
                       "evaluation window sample (96 unique windows)", "scoring rules and exclusions"],
        "not_frozen_yet": ["detector code (Phase 5)", "injector code (Phase 6)", "evaluator code (Phase 6-7)",
                           "implementation hashes -> checkpoint B before Phase 7"],
        "file_sha256": {fn: ingest.sha256_file(ROOT / fn) for fn in files},
        "processed_file_sha256": p3["inputs"]["processed_file_sha256"],
        "raw_files_unchanged": raw_ok,
        "checks": summary["result"].value_counts().to_dict(),
        "evaluation_inspection": summ["inspected_in_evaluation_period"],
    }
    (OUT / "specification_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(summary.to_string(index=False, max_colwidth=80))
    status = "PASS" if (summary["result"] == "PASS").all() else "FAIL"
    print("overall:", status)
    raise SystemExit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
