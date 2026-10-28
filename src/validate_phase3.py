"""Phase 3 checks: inputs unchanged, reconciliation, reproducibility, tests, independent check.

Writes results/phase3/phase3_validation_summary.csv and phase3_validation_manifest.json.
Exit code 0 only when every required check passes; 1 on any failure.

Usage:
    python src/validate_phase3.py
"""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402
import metrics as M  # noqa: E402
import pair_intervals as P  # noqa: E402
import run_phase3  # noqa: E402

ROOT = ingest.ROOT
OUT = ROOT / "results" / "phase3"


def main():
    started = pd.Timestamp.now(tz="UTC")
    rows = []

    def check(cid, name, ok, measured):
        rows.append({"check_id": cid, "check": name, "result": "PASS" if ok else "FAIL", "measured": measured})

    man = json.loads((OUT / "phase3_manifest.json").read_text(encoding="utf-8"))
    src = json.loads((ROOT / "docs/source_manifest.json").read_text(encoding="utf-8"))
    bad = [f["local_path"] for f in src["files"] if ingest.sha256_file(ROOT / f["local_path"]) != f["sha256"]]
    check("P01", "Raw files unchanged (Phase 1 hashes)", not bad, f"{len(src['files']) - len(bad)}/{len(src['files'])}")
    proc = ROOT / "data" / "processed"
    same = {t: ingest.sha256_file(proc / f"{t}.parquet") == h for t, h in man["inputs"]["processed_file_sha256"].items()}
    check("P02", "Phase 2 processed tables unchanged since the Phase 3 run (file SHA-256)", all(same.values()),
          f"{sum(same.values())}/{len(same)}")
    check("P03", "Phase 2 validation status was PASS when Phase 3 ran",
          man["inputs"]["phase2_validation_status"] == "PASS", man["inputs"]["phase2_validation_status"])

    pairs = pd.read_parquet(OUT / "paired_intervals_dev.parquet")
    daily = pd.read_csv(OUT / "daily_pr_dev.csv")
    w = pairs[pairs["in_window"]]
    check("P04", "Window intervals = paired + excluded; each excluded interval has one reason",
          len(w) == int(w["paired"].sum()) + int(w["exclusion_reason"].notna().sum())
          and not (w["paired"] & w["exclusion_reason"].notna()).any(),
          f"{len(w)} = {int(w['paired'].sum())} + {int(w['exclusion_reason'].notna().sum())}")
    check("P05", "Every paired interval uses exactly 3 distinct POA observations; none reused",
          w.loc[w["paired"], ["poa0_observation_id", "poa1_observation_id", "poa2_observation_id"]].stack().is_unique
          and w.loc[w["paired"], ["poa0_observation_id", "poa1_observation_id", "poa2_observation_id"]].notna().all().all(),
          f"{int(w['paired'].sum()) * 3} POA ids")
    check("P06", "Unpaired intervals contribute no energy or irradiation",
          pairs.loc[~pairs["paired"], ["e_ac_kwh", "h_poa_kwh_m2"]].isna().all().all(), "all null")
    check("P07", "Daily E_AC and H_POA equal the sums of paired intervals",
          abs(w["e_ac_kwh"].sum() - daily["e_ac_kwh"].sum()) < 1e-6
          and abs(w["h_poa_kwh_m2"].sum() - daily["h_poa_kwh_m2"].sum()) < 1e-9, "sums equal")
    check("P08", "No DST-ambiguous interval inside the reporting window",
          int((w["meter_state"] == "TIME_AMBIGUOUS").sum()) == 0, "0")
    ok_rep = daily.loc[daily["status"] != "CHECKS_PASSED", "reported_pr"].isna().all() and \
        (daily.loc[daily["status"] == "CHECKS_PASSED", "reported_pr"] ==
         daily.loc[daily["status"] == "CHECKS_PASSED", "diagnostic_pr"]).all()
    check("P09", "reported_pr is null unless CHECKS_PASSED, and equals diagnostic_pr when passed", ok_rep,
          daily["status"].value_counts().to_dict())
    finite = daily["diagnostic_pr"].dropna()
    check("P10", "No PR is infinite; unavailable PR is null, never 0 by substitution",
          bool((finite.abs() != float("inf")).all())
          and daily.loc[daily["h_poa_kwh_m2"].fillna(0) <= 0, "diagnostic_pr"].isna().all(),
          f"{daily['diagnostic_pr'].isna().sum()} null diagnostic PR days")

    cfg = yaml.safe_load(run_phase3.CONFIG.read_text(encoding="utf-8"))
    site = pd.read_parquet(proc / "site_metadata.parquet").iloc[0].to_dict()
    a, b = cfg["development_period"]["start_local_date"], cfg["development_period"]["end_local_date"]
    m = P.add_window_and_clearsky(P.load_stream(proc, "meter_149578", a, b), site, cfg)
    pr2 = P.pair_intervals(m, P.load_stream(proc, "poa_149574", a, b), cfg)
    d2 = M.daily_metrics(pr2, float(site["dc_capacity_kw"]), cfg)
    check("P11", "Re-running pairing and daily metrics reproduces identical content",
          ingest.table_hash(pr2) == man["outputs"]["paired_intervals_dev.parquet"]["content_sha256"]
          and ingest.table_hash(d2) == man["outputs"]["daily_pr_dev.csv"]["content_sha256"], "content hashes equal")

    ind = pd.read_csv(OUT / "independent_check_dev.csv")
    check("P12", "Independent raw-file recomputation matches production for selected development days",
          bool(ind["match"].all()), f"{int(ind['match'].sum())}/{len(ind)} days")

    test = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT, capture_output=True, text=True)
    last = [l for l in test.stdout.strip().splitlines() if l.strip()][-1]
    check("P13", "pytest suite passes", test.returncode == 0, last)

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "phase3_validation_summary.csv", index=False)
    finished = pd.Timestamp.now(tz="UTC")
    status = "PASS" if (summary["result"] == "PASS").all() else "FAIL"
    vman = {"overall_status": status, "exit_code": 0 if status == "PASS" else 1,
            "started_utc": started.isoformat(), "finished_utc": finished.isoformat(),
            "result_counts": summary["result"].value_counts().to_dict(), "pytest": last,
            "file_sha256": {f: ingest.sha256_file(ROOT / f) for f in
                            ["src/validate_phase3.py", "src/independent_check_phase3.py", *run_phase3.CODE,
                             "config/metric_definition.yaml"]}}
    (OUT / "phase3_validation_manifest.json").write_text(json.dumps(vman, indent=2), encoding="utf-8")
    print(summary.to_string(index=False, max_colwidth=90))
    print(f"overall: {status}")
    raise SystemExit(vman["exit_code"])


if __name__ == "__main__":
    main()
