"""Phase 5 validation: frozen spec intact, amendment scope, tests, development reproduction.

Writes results/phase5/validation_summary.csv and results/phase5/validation_manifest.json.
Exit 0 only if every check passes.

Usage:
    python src/validate_phase5.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402

ROOT = ingest.ROOT
OUT = ROOT / "results" / "phase5"
CODE = ["src/evidence.py", "src/checks_basic.py", "src/checks_improved.py", "src/detect.py", "src/detector_inputs.py",
        "src/run_phase5.py", "src/validate_phase5.py", "src/pair_intervals.py", "src/metrics.py"]
CONFIG = ["config/experiment.yaml", "config/experiment_v2.yaml", "config/metric_definition.yaml",
          "config/data_structure.yaml"]
ALLOWED_V2_CHANGES = ("experiment_version", "scoring.rates.")


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def code_tokens(path):
    """Identifiers, arguments, attributes and non-docstring string constants (docstrings/comments excluded)."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docstrings.add(id(node.body[0].value))
    toks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            toks.append(node.id)
        elif isinstance(node, ast.arg):
            toks.append(node.arg)
        elif isinstance(node, ast.Attribute):
            toks.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            toks.append(node.value)
    return toks


def main():
    started = pd.Timestamp.now(tz="UTC")
    rows = []

    def check(cid, name, ok, measured):
        rows.append({"check_id": cid, "check": name, "result": "PASS" if ok else "FAIL", "measured": str(measured)})

    spec = json.loads((ROOT / "results/phase4/specification_manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in spec["file_sha256"].items() if ingest.sha256_file(ROOT / f) != h]
    commit = subprocess.run(["git", "cat-file", "-t", "2952d87"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    check("P5-01", "experiment-v1 specification files unchanged; freeze commit 2952d87 present",
          not bad and commit == "commit", f"{len(spec['file_sha256']) - len(bad)}/{len(spec['file_sha256'])}; {commit}")

    v1 = flatten(yaml.safe_load((ROOT / "config/experiment.yaml").read_text(encoding="utf-8")))
    v2 = flatten(yaml.safe_load((ROOT / "config/experiment_v2.yaml").read_text(encoding="utf-8")))
    changed = [k for k in v1 if v2.get(k) != v1[k] and not k.startswith(ALLOWED_V2_CHANGES)]
    added = sorted(set(v2) - set(v1))
    check("P5-02", "experiment-v2 keeps every v1 value (thresholds, periods, faults, seeds) except version and "
                   "scoring rates", not changed, f"changed outside allowed: {changed}; added keys: {len(added)}")

    test = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT, capture_output=True, text=True)
    last = [line for line in test.stdout.strip().splitlines() if line.strip()][-1]
    check("P5-03", "pytest suite passes", test.returncode == 0, last)

    days = pd.read_csv(OUT / "development_method_results.csv")
    comp = pd.read_csv(OUT / "prototype_comparison.csv")
    counts = {m: days[days["method"] == m]["status"].value_counts().to_dict() for m in ("basic", "improved")}
    expected = {"basic": {"CHECKS_PASSED": 574, "REVIEW_REQUIRED": 123, "INSUFFICIENT_DATA": 33},
                "improved": {"CHECKS_PASSED": 572, "REVIEW_REQUIRED": 123, "INSUFFICIENT_DATA": 35}}
    check("P5-04", "Development statuses, reasons and context codes equal the Phase 4 prototype day by day",
          counts == expected and comp["status_match"].all() and comp["reasons_match"].all()
          and comp["context_match"].all(), counts)

    ci = comp[comp["method"] == "improved"].astype({"implementation_conclusive": bool,
                                                    "timing_conclusive_match": bool, "timing_lag_match": bool})
    lag_diff = ci[~ci["timing_lag_match"]]
    check("P5-05", "Timing: 575 inconclusive days; conclusiveness agrees every day; lag differences only on "
                   "inconclusive days",
          int((~ci["implementation_conclusive"]).sum()) == 575 and ci["timing_conclusive_match"].all()
          and not lag_diff["implementation_conclusive"].any(),
          f"inconclusive {int((~ci['implementation_conclusive']).sum())}; lag differences {len(lag_diff)} "
          f"(all inconclusive)")

    sm = pd.read_csv(OUT / "shared_metric_comparison.csv")
    num = sm.drop(columns=[c for c in sm.columns if not c.endswith(("diff",))])
    check("P5-06", "Shared arithmetic equals the Phase 3 daily table (counts exact, values within 1e-9)",
          (num.abs().max() <= 1e-9).all(), num.abs().max().max())

    rank = {"CHECKS_PASSED": 0, "REVIEW_REQUIRED": 1, "INSUFFICIENT_DATA": 2}
    piv = days.pivot(index="local_reporting_date", columns="method", values="status")
    check("P5-07", "Improved never reports CHECKS_PASSED (or a better status) when basic withholds",
          (piv["improved"].map(rank) >= piv["basic"].map(rank)).all(), f"{len(piv)} days")

    imp = days[days["method"] == "improved"]
    lim = imp[(imp["status"] == "CHECKS_PASSED") & imp["context_codes"].fillna("").str.contains("TIMING_INCONCLUSIVE")]
    check("P5-08", "Every passed day with inconclusive timing carries the 'timing not verified' limitation",
          lim["limitations"].fillna("").str.contains("timing not verified").all(), f"{len(lim)} such days")

    leak = sorted({w for f in CODE[:5] for tok in code_tokens(ROOT / f)
                   for w in ("evaluation_window_sample", "fault", "label", "severity", "scenario")
                   if w in tok.lower()})
    sys.path.insert(0, str(ROOT / "src"))
    import detector_inputs as DI  # noqa: E402
    import evidence as E  # noqa: E402
    obs, _ = DI.load_detector_input(ROOT / "data/processed", "2019-06-01", "2019-06-02", "America/Los_Angeles")
    refs_ok = obs["obs_ref"].str.fullmatch(r"r\d{7}").all()
    check("P5-09", "Detector code reads no labels or window list; input has exactly the contract columns and "
                   "opaque refs", not leak and list(obs.columns) == E.DETECTOR_INPUT_COLUMNS and refs_ok,
          f"forbidden terms in detector modules: {leak}")

    dev = ("2018-02-01", "2020-02-01")
    in_dev = days["local_reporting_date"].between(*dev, inclusive="left").all() and \
        comp["local_reporting_date"].between(*dev, inclusive="left").all()
    check("P5-10", "Only development dates were evaluated (no evaluation-period detector run or PR)", in_dev,
          f"{days['local_reporting_date'].min()} .. {days['local_reporting_date'].max()}")

    srcm = json.loads((ROOT / "docs/source_manifest.json").read_text(encoding="utf-8"))
    raw_ok = all(ingest.sha256_file(ROOT / x["local_path"]) == x["sha256"] for x in srcm["files"])
    proc_ok = all(ingest.sha256_file(ROOT / "data/processed" / f"{t}.parquet") == h
                  for t, h in spec["processed_file_sha256"].items())
    check("P5-11", "Raw files and Phase 2 processed tables unchanged", raw_ok and proc_ok, "raw 22, processed 5")

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "validation_summary.csv", index=False)
    status = "PASS" if (summary["result"] == "PASS").all() else "FAIL"
    import numpy, pvlib  # noqa: E401
    manifest = {
        "phase": 5, "overall_status": status, "exit_code": 0 if status == "PASS" else 1,
        "started_utc": started.isoformat(), "finished_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "experiment_version_used": "experiment-v2 (amends experiment-v1; see docs/amendments.md)",
        "development_period": list(dev), "evaluation_period_touched": False,
        "checks": summary["result"].value_counts().to_dict(), "pytest": last,
        "development_status_counts": counts,
        "timing": {"inconclusive_days": int((~ci["implementation_conclusive"]).sum()), "conclusive_days":
                   int(ci["implementation_conclusive"].sum()), "lag_differences_vs_prototype": len(lag_diff),
                   "cause": "eligible-interval gate: prototype used 5-min sub-interval elevation; implementation uses "
                            "in-window meter membership (A3); all on inconclusive days"},
        "code_sha256": {f: ingest.sha256_file(ROOT / f) for f in CODE},
        "config_sha256": {f: ingest.sha256_file(ROOT / f) for f in CONFIG},
        "test_files_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): ingest.sha256_file(p)
                              for p in sorted((ROOT / "tests").glob("*.py"))},
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": numpy.__version__,
                     "pvlib": pvlib.__version__},
        "note": "Not implementation freeze B: injector and evaluator are Phase 6.",
    }
    (OUT / "validation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(summary.to_string(index=False, max_colwidth=90))
    print("overall:", status)
    raise SystemExit(manifest["exit_code"])


if __name__ == "__main__":
    main()
