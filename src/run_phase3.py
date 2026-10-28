"""Phase 3 runner: daylight-window PR for the declared development period only.

Refuses to run unless the Phase 2 validation passed and the raw files and processed
tables are unchanged. Writes results/phase3/:
  paired_intervals_dev.parquet, daily_pr_dev.csv, reconciliation_dev.csv,
  exclusion_reasons_dev.csv, phase3_manifest.json

Usage:
    python src/run_phase3.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402
import metrics as M  # noqa: E402
import pair_intervals as P  # noqa: E402

ROOT = ingest.ROOT
CONFIG = ROOT / "config" / "metric_definition.yaml"
TABLES = ["site_metadata", "observations", "expected_intervals", "structural_quality_flags", "sampling_segments"]
CODE = ["src/run_phase3.py", "src/pair_intervals.py", "src/metrics.py", "src/timestamps.py", "src/ingest.py"]


def verify_inputs(processed):
    """Phase 2 must have passed; raw and processed inputs must be unchanged."""
    vman = json.loads((ROOT / "results/phase2/validation_manifest.json").read_text(encoding="utf-8"))
    if vman["overall_status"] != "PASS":
        raise SystemExit(f"Phase 2 validation status is {vman['overall_status']}; refusing to run")
    src = json.loads((ROOT / "docs/source_manifest.json").read_text(encoding="utf-8"))
    bad_raw = [f["local_path"] for f in src["files"] if ingest.sha256_file(ROOT / f["local_path"]) != f["sha256"]]
    rman = json.loads((ROOT / "results/phase2/run_manifest.json").read_text(encoding="utf-8"))
    rows_ok = {t: len(pd.read_parquet(processed / f"{t}.parquet")) == rman["tables"][t]["rows"] for t in TABLES}
    if bad_raw or not all(rows_ok.values()):
        raise SystemExit(f"inputs changed: raw={bad_raw} rows={rows_ok}")
    return {"phase2_validation_status": vman["overall_status"],
            "phase2_validation_finished_utc": vman["finished_utc"],
            "raw_files_unchanged": f"{len(src['files'])}/{len(src['files'])}",
            "processed_file_sha256": {t: ingest.sha256_file(processed / f"{t}.parquet") for t in TABLES}}


def unflagged_zero_days(pairs, daily, capacity, cfg):
    """Paired in-window observed zeros WITHOUT the Phase 2 daylight-zero flag (they withhold nothing).

    For each affected day, a comparison PR with those intervals removed is reported as a
    diagnostic only; it is not a reporting rule.
    """
    w = pairs[pairs["in_window"] & pairs["paired"]]
    z = w[(w["meter_state"] == "OBSERVED_ZERO") & ~w["meter_flags"].str.contains("DAYLIGHT_ZERO_UNRESOLVED")]
    rows = []
    for day, g in z.groupby("local_reporting_date"):
        dp = w[w["local_reporting_date"] == day]
        keep = dp[~dp.index.isin(g.index)]
        drow = daily[daily["local_reporting_date"] == day].iloc[0]
        pr_without = M.performance_ratio(keep["e_ac_kwh"].sum(), capacity, keep["h_poa_kwh_m2"].sum(),
                                         cfg["g_stc_kw_m2"])
        rows.append({"local_reporting_date": day, "status": drow["status"], "n_unflagged_paired_zeros": len(g),
                     "min_elevation_deg": round(float(g["solar_elevation_deg"].min()), 2),
                     "max_elevation_deg": round(float(g["solar_elevation_deg"].max()), 2),
                     "share_of_paired_h": g["h_poa_kwh_m2"].sum() / dp["h_poa_kwh_m2"].sum(),
                     "diagnostic_pr": drow["diagnostic_pr"],
                     "comparison_pr_without_these_zeros": pr_without,
                     "pp_difference": M.percentage_point_difference(drow["diagnostic_pr"], pr_without)})
    return pd.DataFrame(rows)


def reconcile(pairs, daily):
    w = pairs[pairs["in_window"]]
    rows = [
        ("expected meter intervals (dev period)", len(pairs)),
        ("outside reporting window", int((~pairs["in_window"]).sum())),
        ("inside reporting window", len(w)),
        ("  fully paired", int(w["paired"].sum())),
        ("  excluded", int((~w["paired"]).sum())),
    ]
    for r, n in w["exclusion_reason"].value_counts().sort_index().items():
        rows.append((f"    excluded: {r}", int(n)))
    rows += [
        ("window intervals that are DST-ambiguous (must be 0)", int((w["meter_state"] == "TIME_AMBIGUOUS").sum())),
        ("POA observations used in pairs (3 per pair)", int(w.loc[w["paired"], ["poa0_observation_id",
                                                                              "poa1_observation_id",
                                                                              "poa2_observation_id"]].stack().nunique())),
        ("sum interval E_AC kWh (pairs)", round(float(w["e_ac_kwh"].sum()), 6)),
        ("sum daily E_AC kWh", round(float(daily["e_ac_kwh"].sum()), 6)),
        ("sum interval H_POA kWh/m2 (pairs)", round(float(w["h_poa_kwh_m2"].sum()), 6)),
        ("sum daily H_POA kWh/m2", round(float(daily["h_poa_kwh_m2"].sum()), 6)),
        ("local reporting days", len(daily)),
    ]
    for s, n in daily["status"].value_counts().sort_index().items():
        rows.append((f"days {s}", int(n)))
    return pd.DataFrame(rows, columns=["item", "value"])


def main():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    dcfg = ingest.load_config()
    processed = ROOT / dcfg["outputs"]["processed_dir"]
    out = ROOT / cfg["outputs"]["results_dir"]
    out.mkdir(parents=True, exist_ok=True)
    started = pd.Timestamp.now(tz="UTC")
    inputs = verify_inputs(processed)

    site = pd.read_parquet(processed / "site_metadata.parquet").iloc[0].to_dict()
    capacity = float(site["dc_capacity_kw"])
    if capacity != cfg["capacity"]["expected_kw"]:
        raise SystemExit(f"capacity {capacity} differs from configured {cfg['capacity']['expected_kw']}")
    dev = cfg["development_period"]
    a, b = dev["start_local_date"], dev["end_local_date"]
    meter = P.load_stream(processed, "meter_149578", a, b)
    poa = P.load_stream(processed, "poa_149574", a, b)
    meter = P.add_window_and_clearsky(meter, site, cfg)
    pairs = P.pair_intervals(meter, poa, cfg)
    daily = M.daily_metrics(pairs, capacity, cfg)

    pairs.to_parquet(out / "paired_intervals_dev.parquet", index=False)
    daily.to_csv(out / "daily_pr_dev.csv", index=False)
    rec = reconcile(pairs, daily)
    uz = unflagged_zero_days(pairs, daily, capacity, cfg)
    uz.to_csv(out / "unflagged_zero_days_dev.csv", index=False)
    passed = daily[daily["status"] == M.CHECKS_PASSED]
    extra = [
        ("paired window zeros WITHOUT daylight-zero flag (withhold nothing)", int(uz["n_unflagged_paired_zeros"].sum())
         if len(uz) else 0),
        ("  days affected", len(uz)),
        ("  of which CHECKS_PASSED", int((uz["status"] == M.CHECKS_PASSED).sum()) if len(uz) else 0),
        ("  median share of day's paired H on CHECKS_PASSED days",
         round(float(uz.loc[uz["status"] == M.CHECKS_PASSED, "share_of_paired_h"].median()), 5) if len(uz) else 0),
        ("  max pp PR difference on CHECKS_PASSED days (diagnostic comparison)",
         round(float(uz.loc[uz["status"] == M.CHECKS_PASSED, "pp_difference"].abs().max()), 3) if len(uz) else 0),
        ("passed days with H >= 3 kWh/m2 and reported PR > 0.85",
         int(((passed["h_poa_kwh_m2"] >= 3) & (passed["reported_pr"] > 0.85)).sum())),
        ("passed days with reported PR > 0.85: max H kWh/m2",
         round(float(passed.loc[passed["reported_pr"] > 0.85, "h_poa_kwh_m2"].max()), 3)),
    ]
    rec = pd.concat([rec, pd.DataFrame(extra, columns=["item", "value"])], ignore_index=True)
    rec.to_csv(out / "reconciliation_dev.csv", index=False)
    w = pairs[pairs["in_window"]]
    (w.groupby(["exclusion_reason"], dropna=False).agg(intervals=("interval_hours", "size"),
                                                        hours=("interval_hours", "sum"))
     .reset_index().to_csv(out / "exclusion_reasons_dev.csv", index=False))

    finished = pd.Timestamp.now(tz="UTC")
    manifest = {
        "metric_version": cfg["metric_version"],
        "development_period": [a, b],
        "started_utc": started.isoformat(), "finished_utc": finished.isoformat(),
        "inputs": inputs,
        "config_sha256": {"config/metric_definition.yaml": ingest.sha256_file(CONFIG),
                          "config/data_structure.yaml": ingest.sha256_file(ROOT / "config/data_structure.yaml")},
        "code_sha256": {f: ingest.sha256_file(ROOT / f) for f in CODE},
        "capacity_kw": capacity,
        "outputs": {"paired_intervals_dev.parquet": {"rows": len(pairs), "content_sha256": ingest.table_hash(pairs)},
                    "daily_pr_dev.csv": {"rows": len(daily), "content_sha256": ingest.table_hash(daily)}},
        "status_counts": daily["status"].value_counts().to_dict(),
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__,
                     "pvlib": pvlib.__version__},
    }
    (out / "phase3_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(rec.to_string(index=False))


if __name__ == "__main__":
    main()
