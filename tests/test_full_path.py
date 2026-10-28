"""Full path on synthetic data: observations -> injection -> evidence -> detector -> evaluator.

Expected outcomes are specified from the construction (clear-sky POA, PR 0.8, timing lag 0),
not from the scorer: e.g. a 240-min gap in a ~14.5 h window leaves ~72% time coverage; a 60-min
hold-last flatline makes 13 identical readings (>= 6); a 15-min one makes 4 (< 6); a +30 min
POA-only shift moves the best lag to -30.
"""
import inspect

import pytest

import detect as D
import evaluate as EV
import evidence as E
import inject_faults as INJ
from synthetic import DAY, EXP3, MCFG, META, SITE, days_around, make_obs_days

SMAP = EV.specific_map(EXP3)
BASELINE = 0          # constructed reference: lag 0 by construction


def run_case(family, scenario, severity):
    obs = make_obs_days(days_around(DAY))
    plan = INJ.plan_window(f"fullpath-{family}", family, DAY, SITE, MCFG, EXP3)
    d0, _ = INJ.reassign_refs(obs)
    c0, days0, _ = D.evaluate(d0, SITE, META, MCFG, EXP3, BASELINE, [DAY])
    faulted, truth = INJ.apply_case(obs, plan, scenario, severity, SITE, META, EXP3)
    d1, _ = INJ.reassign_refs(faulted)
    assert list(d1.columns) == E.DETECTOR_INPUT_COLUMNS                   # only contract fields reach detector
    c1, days1, _ = D.evaluate(d1, SITE, META, MCFG, EXP3, BASELINE, [DAY])
    out = {}
    for m in ("basic", "improved"):
        out[m] = EV.score_case(m, family, truth["injection_status"], days0[days0.method == m].iloc[0].to_dict(),
                               days1[days1.method == m].iloc[0].to_dict(), c0[c0.method == m], c1[c1.method == m],
                               SMAP)
    return out, truth


def test_unmodified_counterpart_passes_both_methods():
    obs = make_obs_days(days_around(DAY))
    d0, _ = INJ.reassign_refs(obs)
    _, days0, _ = D.evaluate(d0, SITE, META, MCFG, EXP3, BASELINE, [DAY])
    assert set(days0["status"]) == {"CHECKS_PASSED"}
    assert days0["diagnostic_pr"].iloc[0] == pytest.approx(0.8, abs=1e-9)


def test_large_missing_block_detected_by_both_methods():
    s, truth = run_case("missing_daytime", "primary:rows_removed", 240)
    assert truth["n_rows_affected"] == 48
    for m in ("basic", "improved"):
        assert s[m]["category"] == "DETECTED_SPECIFIC" and "LOW_TIME_COVERAGE" in s[m]["new_specific_reasons"]


def test_flatline_detected_only_by_improved_and_short_flatline_missed():
    s, _ = run_case("flatline_poa", "primary:hold_last", 60)
    assert s["improved"]["category"] == "DETECTED_SPECIFIC" and s["improved"]["new_specific_reasons"] == "POA_STALE"
    assert s["basic"]["category"] == "MISSED"
    s, _ = run_case("flatline_poa", "primary:hold_last", 15)
    assert s["improved"]["category"] == "MISSED" and s["improved"]["specific_check_outcome_faulted"] == "I2:PASS"


def test_poa_shift_detected_by_timing_with_pr_effect():
    s, truth = run_case("timestamp_shift", "primary:poa_only", 30)
    assert truth["n_collisions"] == 6
    assert s["improved"]["category"] == "DETECTED_SPECIFIC"
    assert s["improved"]["timing_applicable"] is True
    assert s["basic"]["category"] in ("MISSED", "WITHHELD_OTHER") and s["basic"]["category"] != "DETECTED_SPECIFIC"
    assert s["basic"]["pr_pp_difference"] != 0 and s["basic"]["pr_pp_difference"] == s["improved"]["pr_pp_difference"]


def test_detector_signature_excludes_truth():
    params = set(inspect.signature(D.evaluate).parameters)
    assert not params & {"truth", "case", "scenario", "severity", "label", "window_id"}
