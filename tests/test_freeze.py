"""Freeze-B verification tests on a temporary bundle (the real frozen files are never altered)."""
import json
import shutil
from pathlib import Path

import pytest

import freeze_b as FB

ROOT = Path(__file__).resolve().parents[1]


def bundle(tmp_path):
    files = ["src/inject_faults.py", "src/evaluate.py", "config/experiment_v3.yaml",
             "results/phase4/evaluation_window_sample.csv"]
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, tmp_path / f)
    return files, FB.create(tmp_path, files=files, processed=False)


def test_intact_copy_verifies(tmp_path):
    _, m = bundle(tmp_path)
    assert FB.verify(tmp_path, m) == []


def test_changed_missing_and_version_drift_are_reported(tmp_path):
    files, m = bundle(tmp_path)
    with open(tmp_path / "config/experiment_v3.yaml", "a", encoding="utf-8") as fh:
        fh.write("\n# tampered\n")
    (tmp_path / "src/evaluate.py").unlink()
    m2 = json.loads(json.dumps(m))
    m2["versions"]["pandas"] = "0.0.0"
    problems = FB.verify(tmp_path, m2)
    assert "changed: config/experiment_v3.yaml" in problems
    assert "missing: src/evaluate.py" in problems
    assert any(p.startswith("version pandas") for p in problems)


def test_runner_refuses_to_evaluate_on_mismatch(tmp_path, monkeypatch):
    import run_evaluation as RE
    files, m = bundle(tmp_path)
    (tmp_path / "results/phase6").mkdir(parents=True, exist_ok=True)
    (tmp_path / RE.FREEZE).write_text(json.dumps(m), encoding="utf-8")
    with open(tmp_path / "src/inject_faults.py", "a", encoding="utf-8") as fh:
        fh.write("\n# tampered\n")
    monkeypatch.setattr(RE, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        RE.main(["evaluate", "--run-id", "should-not-run"])
    assert "freeze B mismatch" in str(exc.value)
    assert not (tmp_path / "results/phase7").exists()
    with pytest.raises(SystemExit):
        RE.main(["verify-freeze"])


def test_real_freeze_manifest_verifies_if_present():
    path = ROOT / "results/phase6/implementation_freeze_manifest.json"
    if not path.exists():
        pytest.skip("freeze B not created yet")
    assert FB.verify(ROOT, FB.load(ROOT)) == []
