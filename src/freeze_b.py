"""Implementation freeze B: hash the files and inputs used at evaluation runtime, and verify them.

The manifest does not hash itself (no circular self-hashing). verify() works against any
root directory, so tests can exercise it on a temporary copy without touching real files.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RUNTIME_CODE = ["src/evidence.py", "src/checks_basic.py", "src/checks_improved.py", "src/detect.py",
                "src/detector_inputs.py", "src/pair_intervals.py", "src/metrics.py", "src/timestamps.py",
                "src/standardize.py", "src/ingest.py", "src/inject_faults.py", "src/evaluate.py",
                "src/run_evaluation.py", "src/freeze_b.py"]
CONFIG_AND_SPEC = ["config/experiment_v3.yaml", "config/experiment_v2.yaml", "config/experiment.yaml",
                   "config/metric_definition.yaml", "config/data_structure.yaml", "docs/amendments.md",
                   "docs/experiment_protocol.md", "docs/check_definitions.md", "docs/evaluation_runbook.md"]
FROZEN_INPUTS = ["results/phase4/evaluation_window_sample.csv", "results/phase4/development_analysis_summary.json",
                 "results/phase4/evaluation_structural_eligibility.csv", "results/phase4/specification_manifest.json",
                 "docs/source_manifest.json", "requirements.txt"]
TESTS = ["tests/conftest.py", "tests/test_detectors.py", "tests/test_inject_faults.py", "tests/test_evaluate.py",
         "tests/test_full_path.py", "tests/test_freeze.py", "tests/synthetic.py"]
PROCESSED = ["site_metadata", "observations", "expected_intervals", "structural_quality_flags", "sampling_segments"]
PACKAGES = ["pandas", "numpy", "pyarrow", "pvlib", "PyYAML", "scipy"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def versions():
    import importlib.metadata as md
    v = {"python": sys.version.split()[0]}
    for p in PACKAGES:
        try:
            v[p] = md.version(p)
        except md.PackageNotFoundError:
            v[p] = None
    return v


def create(root, files=None, processed=True, extra=None):
    root = Path(root)
    files = files or (RUNTIME_CODE + CONFIG_AND_SPEC + FROZEN_INPUTS + TESTS)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    m = {"checkpoint": "B - implementation freeze (end of Phase 6, before any evaluation case)",
         "experiment_version": "experiment-v3",
         "base_git_commit": head or None,
         "note": "This manifest is committed in the freeze commit; it does not hash itself. "
                 "The freeze commit id is recorded in docs/decision_log.md of the following phase.",
         "file_sha256": {f: sha256(root / f) for f in files},
         "processed_file_sha256": {t: sha256(root / "data/processed" / f"{t}.parquet") for t in PROCESSED}
         if processed else {},
         "versions": versions()}
    if extra:
        m.update(extra)
    return m


def verify(root, manifest, check_versions=True):
    """Return a list of problems (empty = frozen state intact)."""
    root = Path(root)
    problems = []
    for f, h in manifest["file_sha256"].items():
        p = root / f
        if not p.exists():
            problems.append(f"missing: {f}")
        elif sha256(p) != h:
            problems.append(f"changed: {f}")
    for t, h in manifest.get("processed_file_sha256", {}).items():
        p = root / "data/processed" / f"{t}.parquet"
        if not p.exists():
            problems.append(f"missing processed: {t}")
        elif sha256(p) != h:
            problems.append(f"changed processed: {t}")
    if check_versions:
        now = versions()
        for k, v in manifest.get("versions", {}).items():
            if now.get(k) != v:
                problems.append(f"version {k}: frozen {v}, now {now.get(k)}")
    return problems


def load(root, rel="results/phase6/implementation_freeze_manifest.json"):
    return json.loads((Path(root) / rel).read_text(encoding="utf-8"))
