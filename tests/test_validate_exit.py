"""Failure propagation of the structural validator, without loading the real dataset."""
import json

import pandas as pd
import pytest

import validate_structure as V


def rows_with(*results):
    rows = []
    for i, r in enumerate(results):
        V.check(rows, f"T{i}", "synthetic", r if isinstance(r, str) else r, "m")
    return rows


def test_all_required_pass_exits_zero_even_with_info(tmp_path):
    code = V.finalize(rows_with(True, True, "INFO"), tmp_path, pd.Timestamp.now(tz="UTC"), False)
    assert code == 0
    man = json.loads((tmp_path / "validation_manifest.json").read_text())
    assert man["overall_status"] == "PASS" and man["result_counts"]["INFO"] == 1


def test_required_failure_exits_one_and_is_saved(tmp_path):
    code = V.finalize(rows_with(True, False, "INFO"), tmp_path, pd.Timestamp.now(tz="UTC"), False)
    assert code == 1
    saved = pd.read_csv(tmp_path / "structural_validation_summary.csv")
    assert list(saved["result"]) == ["PASS", "FAIL", "INFO"]
    assert json.loads((tmp_path / "validation_manifest.json").read_text())["required_failed"] == ["T1"]


def test_skipped_required_check_is_incomplete_not_pass(tmp_path):
    code = V.finalize(rows_with(True, "SKIPPED"), tmp_path, pd.Timestamp.now(tz="UTC"), True)
    assert code == 2
    assert json.loads((tmp_path / "validation_manifest.json").read_text())["overall_status"] == "INCOMPLETE"


def test_cli_raises_nonzero_exit_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "main", lambda skip_rerun: rows_with(True, False))
    monkeypatch.setattr(V, "ROOT", tmp_path)
    (tmp_path / "results" / "phase2").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        V.cli([])
    assert exc.value.code == 1
    assert (tmp_path / "results" / "phase2" / "structural_validation_summary.csv").exists()
