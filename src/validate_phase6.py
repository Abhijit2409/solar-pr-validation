"""Phase 6 validation: preserved specs, amendment scope, tests, smoke run, freeze B, no evaluation executed.

Writes results/phase6/validation_summary.csv and results/phase6/validation_manifest.json.

Usage:
    python src/validate_phase6.py
"""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze_b as FB  # noqa: E402
import ingest  # noqa: E402
from validate_phase5 import flatten  # noqa: E402

ROOT = ingest.ROOT
OUT = ROOT / "results" / "phase6"
V3_ALLOWED = ("experiment_version", "amends", "amendments.", "injection.", "freeze.experiment_v")


def main():
    started = pd.Timestamp.now(tz="UTC")
    rows = []

    def check(cid, name, ok, measured):
        rows.append({"check_id": cid, "check": name, "result": "PASS" if ok else "FAIL", "measured": str(measured)})

    spec = json.loads((ROOT / "results/phase4/specification_manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in spec["file_sha256"].items() if ingest.sha256_file(ROOT / f) != h]
    commits = [subprocess.run(["git", "cat-file", "-t", c], cwd=ROOT, capture_output=True, text=True).stdout.strip()
               for c in ("2952d87", "f1ff436")]
    check("P6-01", "experiment-v1 freeze files unchanged; commits 2952d87 and f1ff436 present",
          not bad and commits == ["commit", "commit"], f"{len(spec['file_sha256']) - len(bad)}/{len(spec['file_sha256'])}")

    p5 = json.loads((ROOT / "results/phase5/validation_manifest.json").read_text(encoding="utf-8"))
    p5bad = [f for f, h in {**p5["code_sha256"], **p5["config_sha256"]}.items() if ingest.sha256_file(ROOT / f) != h]
    check("P6-02", "Phase 5 code and configs (incl. experiment-v2) unchanged", not p5bad, p5bad or "all match")

    v2 = flatten(yaml.safe_load((ROOT / "config/experiment_v2.yaml").read_text(encoding="utf-8")))
    v3 = flatten(yaml.safe_load((ROOT / "config/experiment_v3.yaml").read_text(encoding="utf-8")))
    changed = [k for k in v2 if v3.get(k) != v2[k] and not k.startswith(V3_ALLOWED)]
    added = [k for k in v3 if k not in v2 and not k.startswith(V3_ALLOWED)]
    check("P6-03", "experiment-v3 keeps every v2 value; only version, amendment notes, injection rules and freeze "
                   "notes added", not changed and not added, f"changed {changed}; unexpected added {added}")

    test = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT, capture_output=True, text=True)
    last = [line for line in test.stdout.strip().splitlines() if line.strip()][-1]
    check("P6-04", "pytest suite passes (incl. real freeze-B verification test)", test.returncode == 0
          and "skipped" not in last, last)

    sm = ROOT / "results/phase6/smoke"
    meta = json.loads((sm / "smoke_metadata.json").read_text(encoding="utf-8"))
    truth = pd.read_csv(sm / "injection_truth.csv")
    days = pd.read_csv(sm / "detector_day_results.csv")
    sample = pd.read_csv(ROOT / "results/phase4/evaluation_window_sample.csv")
    dev_ok = all("2018-02-01" <= d < "2020-02-01" for d in meta["days"])
    no_overlap = not (set(truth["window_id"]) & set(sample["window_id"])) and \
        not (set(meta["days"]) & set(sample["local_reporting_date"]))
    separated = not ({"case_id", "scenario", "severity", "family", "window_id"} & set(days.columns))
    check("P6-05", "Smoke cases are development-only, disjoint from the frozen sample, and detector outputs carry "
                   "no truth columns", dev_ok and no_overlap and separated and meta["label"].startswith("DEVELOPMENT"),
          f"{meta['windows']} windows, {meta['cases']} cases, statuses {truth['injection_status'].value_counts().to_dict()}")

    fm = FB.load(ROOT)
    problems = FB.verify(ROOT, fm)
    self_hashed = "results/phase6/implementation_freeze_manifest.json" in fm["file_sha256"]
    check("P6-06", "Freeze B verifies (36 files, 5 processed tables, versions) and does not hash itself",
          not problems and not self_hashed and len(fm["file_sha256"]) == 36, problems or "verified")

    check("P6-07", "No evaluation case executed (results/phase7 absent)", not (ROOT / "results/phase7").exists(),
          "absent")

    srcm = json.loads((ROOT / "docs/source_manifest.json").read_text(encoding="utf-8"))
    raw_ok = all(ingest.sha256_file(ROOT / x["local_path"]) == x["sha256"] for x in srcm["files"])
    check("P6-08", "Raw files unchanged", raw_ok, "22/22" if raw_ok else "changed")

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "validation_summary.csv", index=False)
    status = "PASS" if (summary["result"] == "PASS").all() else "FAIL"
    manifest = {"phase": 6, "overall_status": status, "exit_code": 0 if status == "PASS" else 1,
                "started_utc": started.isoformat(), "finished_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                "experiment_version_active": "experiment-v3", "pytest": last,
                "checks": summary["result"].value_counts().to_dict(),
                "smoke": meta,
                "freeze_b_manifest": "results/phase6/implementation_freeze_manifest.json",
                "freeze_b_manifest_sha256": FB.sha256(ROOT / "results/phase6/implementation_freeze_manifest.json"),
                "evaluation_executed": False,
                "validator_sha256": FB.sha256(ROOT / "src/validate_phase6.py")}
    (OUT / "validation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(summary.to_string(index=False, max_colwidth=90))
    print("overall:", status)
    raise SystemExit(manifest["exit_code"])


if __name__ == "__main__":
    main()
