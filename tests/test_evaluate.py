"""Scorer tests with hand-specified day results (expected categories written from the protocol, not the code)."""
import math

import pandas as pd
import pytest

import evaluate as EV
from synthetic import EXP3

SMAP = EV.specific_map(EXP3)


def day(status, reasons="", context="", pr=0.80):
    return {"status": status, "reason_codes": reasons, "context_codes": context, "diagnostic_pr": pr}


def checks(**outcomes):
    return pd.DataFrame({"check_id": list(outcomes), "outcome": list(outcomes.values())})


P, R, X = "CHECKS_PASSED", "REVIEW_REQUIRED", "INSUFFICIENT_DATA"


@pytest.mark.parametrize("method,family,inj,orig,faulted,fchecks,expected", [
    # injection failed beats an already-withheld original
    ("improved", "flatline_poa", "UNSCORABLE", day(R, "MATERIAL_ZERO_OUTPUT"), None, None, "UNSCORABLE"),
    # already withheld before injection is never a new detection, even if the fault adds the specific reason
    ("improved", "flatline_poa", "APPLIED", day(R, "MATERIAL_ZERO_OUTPUT"), day(R, "MATERIAL_ZERO_OUTPUT;POA_STALE"),
     checks(I2="FLAGGED"), "ALREADY_WITHHELD"),
    ("improved", "flatline_poa", "APPLIED", day(P), day(R, "POA_STALE"), checks(I2="FLAGGED"), "DETECTED_SPECIFIC"),
    ("basic", "missing_daytime", "APPLIED", day(P), day(X, "LOW_TIME_COVERAGE"), checks(B1="FLAGGED"),
     "DETECTED_SPECIFIC"),
    ("basic", "timestamp_shift", "APPLIED", day(P), day(R, "SUSPICIOUS_ZERO_OUTPUT"), checks(), "WITHHELD_OTHER"),
    ("improved", "timestamp_shift", "APPLIED", day(P), day(P, context="TIMING_INCONCLUSIVE"),
     checks(I3="INCONCLUSIVE"), "INCONCLUSIVE"),
    ("improved", "timestamp_shift", "APPLIED", day(P), day(P), checks(I3="PASS"), "MISSED"),
    # basic has no timing check, so a passed faulted day is a miss, never inconclusive
    ("basic", "timestamp_shift", "APPLIED", day(P), day(P), checks(), "MISSED"),
    ("basic", "flatline_poa", "APPLIED", day(P), day(P), checks(), "MISSED"),
])
def test_category_precedence(method, family, inj, orig, faulted, fchecks, expected):
    s = EV.score_case(method, family, inj, orig, faulted, checks(), fchecks, SMAP)
    assert s["category"] == expected


def test_specific_checks_follow_method():
    assert SMAP["timestamp_shift"]["basic"] == (set(), set())
    assert SMAP["timestamp_shift"]["improved"] == ({"TIMING_INCONSISTENT"}, {"I3"})
    assert SMAP["missing_daytime"]["basic"][0] == {"LOW_TIME_COVERAGE", "NO_PAIRED_DATA"}
    assert "LOW_CLEARSKY_WEIGHTED_COVERAGE" in SMAP["missing_daytime"]["improved"][0]


def test_inconclusive_timing_stays_visible_when_other_check_withholds():
    s = EV.score_case("improved", "timestamp_shift", "APPLIED", day(P),
                      day(R, "SUSPICIOUS_ZERO_OUTPUT", "TIMING_INCONCLUSIVE"), checks(),
                      checks(I3="INCONCLUSIVE", B5b="FLAGGED"), SMAP)
    assert s["category"] == "WITHHELD_OTHER"
    assert s["timing_outcome_faulted"] == "I3:INCONCLUSIVE" and s["timing_applicable"] is False
    assert s["reporting_withheld"] is True and "TIMING_INCONCLUSIVE" in s["faulted_context"]


def test_pr_effect_units_sign_and_unavailable():
    pp, rel, na = EV.pr_effect(0.80, 0.76)
    assert pp == pytest.approx(-4.0) and rel == pytest.approx(-5.0) and not na   # percentage points vs percent
    pp, rel, na = EV.pr_effect(0.0, 0.1)
    assert pp == pytest.approx(10.0) and rel is None
    assert EV.pr_effect(float("nan"), 0.8) == (None, None, True)
    assert EV.pr_effect(0.8, None) == (None, None, True)


def scores_fixture():
    cats = ["UNSCORABLE", "ALREADY_WITHHELD", "ALREADY_WITHHELD", "DETECTED_SPECIFIC", "DETECTED_SPECIFIC",
            "DETECTED_SPECIFIC", "DETECTED_SPECIFIC", "WITHHELD_OTHER", "INCONCLUSIVE", "MISSED"]
    return pd.DataFrame({"method": "improved", "family": "timestamp_shift", "category": cats,
                         "window_id": ["w1", "w1", "w2", "w2", "w3", "w3", "w4", "w4", "w5", "w5"],
                         "timing_applicable": [None, None, None, True, True, True, True, False, False, True],
                         "pr_unavailable": [False] * 9 + [True],
                         "pr_pp_difference": [None, None, None, -3.0, -1.0, 2.0, -5.0, -0.5, 0.0, None]})


def test_rates_keep_inconclusive_in_headline_denominator():
    s = EV.summarize(scores_fixture(), ["method", "family"]).iloc[0]
    assert s["cases"] == 10 and s["unique_windows"] == 5
    assert s["headline_denominator"] == 7 and s["headline_detection_rate"] == pytest.approx(4 / 7)
    assert s["conclusive_denominator"] == 6 and s["conclusive_only_detection_rate"] == pytest.approx(4 / 6)
    assert s["withheld_numerator"] == 5 and s["withheld_rate"] == pytest.approx(5 / 7)
    assert s["timing_applicable_numerator"] == 5 and s["timing_applicable_denominator"] == 7
    assert s["pr_unavailable"] == 1 and s["pr_pp_median"] == pytest.approx(-0.75)


def test_zero_denominators_give_null_rates():
    sc = scores_fixture()
    sc["category"] = ["UNSCORABLE"] * 5 + ["ALREADY_WITHHELD"] * 5
    s = EV.summarize(sc, ["method"]).iloc[0]
    assert s["headline_denominator"] == 0 and s["headline_detection_rate"] is None
    assert s["withheld_rate"] is None and s["conclusive_only_detection_rate"] is None


def test_alert_burden_counts_unique_days():
    d = pd.DataFrame({"method": ["basic"] * 5, "local_reporting_date": ["d1", "d1", "d1", "d2", "d3"],
                      "status": [R, R, R, P, X]})
    a = EV.alert_burden(d, "test").iloc[0]
    assert a["unique_days"] == 3 and a["unconfirmed_alert_days"] == 2
    assert a["unconfirmed_alert_share"] == pytest.approx(2 / 3) and a["reporting_coverage_retained"] == pytest.approx(1 / 3)
