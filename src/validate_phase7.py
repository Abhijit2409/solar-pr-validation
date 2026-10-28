"""Phase 7 validation: run1 completeness and immutability, freeze B after execution, audit, analysis hashes.

Writes results/phase7/validation_manifest.json, results/phase7/validation_summary.csv and a slim,
commit-sized truth summary results/phase7/analysis/injection_truth_summary.csv (no observation lists).

Usage:
    python src/validate_phase7.py
"""
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze_b as FB  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P7 = ROOT / "results/phase7"
RUN = P7 / "run1"
AN = P7 / "analysis"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    rows = []

    def check(cid, name, ok, measured):
        rows.append({"check_id": cid, "check": name, "result": "PASS" if ok else "FAIL", "measured": str(measured)})

    exit_code = (P7 / "run1_exit_code.txt").read_text().strip()
    meta = json.loads((RUN / "run_metadata.json").read_text(encoding="utf-8"))
    check("P7-01", "Process exit code 0 and COMPLETED marker present", exit_code == "0" and (RUN / "COMPLETED").exists(),
          f"exit {exit_code}")
    check("P7-02", "Run metadata: experiment-v3, 96 windows, 672 cases, 768 detector runs, 0 failures",
          (meta["experiment_version"], meta["windows"], meta["cases"], meta["detector_runs"], meta["failures"])
          == ("experiment-v3", 96, 672, 768, 0), meta)
    at = json.loads((AN / "run1_output_hashes_at_completion.json").read_text(encoding="utf-8"))
    now = {p.name: sha(p) for p in sorted(RUN.iterdir())}
    check("P7-03", "run1 outputs byte-identical to their hashes recorded at completion (analysis read-only)",
          now == at["run1_output_sha256"], f"{len(now)} files")
    fm = FB.load(ROOT)
    problems = FB.verify(ROOT, fm)
    check("P7-04", "Freeze B verifies after execution; run used the freeze manifest hash", not problems
          and meta["freeze_manifest_sha256"] == FB.sha256(ROOT / "results/phase6/implementation_freeze_manifest.json"),
          problems or "verified")
    audit = pd.read_csv(AN / "scoring_audit.csv")
    check("P7-05", "Independent scoring audit (AI-assisted; separate code) passes", (audit["result"] == "PASS").all(),
          f"{int((audit['result'] == 'PASS').sum())}/{len(audit)} items")
    rec = pd.read_csv(AN / "case_reconciliation.csv")
    check("P7-06", "Reconciliation: planned = attempted = applied + unscorable; no failed windows; 2 scores per case",
          (rec["planned"] == rec["attempted"]).all() and (rec["attempted"] == rec["applied"] + rec["unscorable"]).all()
          and rec["failed_windows"].eq(0).all() and (rec["scored_rows"] == 2 * rec["attempted"]).all(),
          f"planned {int(rec['planned'].sum())}, applied {int(rec['applied'].sum())}, unscorable "
          f"{int(rec['unscorable'].sum())}")

    truth = pd.read_csv(RUN / "injection_truth.csv")
    slim = truth.drop(columns=["affected_refs", "affected_observation_ids", "collisions"])
    slim.to_csv(AN / "injection_truth_summary.csv", index=False)
    check("P7-07", "Slim truth summary written (full truth kept in run1, excluded from Git by size)", len(slim) == 672,
          f"{len(slim)} rows")

    summary = pd.DataFrame(rows)
    summary.to_csv(P7 / "validation_summary.csv", index=False)
    status = "PASS" if (summary["result"] == "PASS").all() else "FAIL"
    manifest = {"phase": 7, "overall_status": status, "run_id": "run1", "run_dir": "results/phase7/run1",
                "experiment_version": meta["experiment_version"], "process_exit_code": int(exit_code),
                "run_started_utc": meta["started_utc"], "run_finished_utc": meta["finished_utc"],
                "run1_output_sha256": now,
                "execution_log_sha256": sha(P7 / "run1_execution.log"),
                "freeze_b_manifest_sha256": meta["freeze_manifest_sha256"],
                "freeze_b_verified_after_run": not problems,
                "analysis_sha256": {p.name: sha(p) for p in sorted(AN.iterdir())},
                "audit": "AI-assisted independent re-scoring (src/audit_phase7.py); no human review performed",
                "checks": summary["result"].value_counts().to_dict(),
                "validator_sha256": sha(ROOT / "src/validate_phase7.py")}
    (P7 / "validation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(summary.to_string(index=False, max_colwidth=90))
    print("overall:", status)
    raise SystemExit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
