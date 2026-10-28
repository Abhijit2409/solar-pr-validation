"""Phase 3 known-answer and edge-case tests on synthetic streams (no real data)."""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import metrics as M
import pair_intervals as P

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "metric_definition.yaml").read_text())
T0 = pd.Timestamp("2019-06-10 18:00", tz="UTC")   # 11:00 PDT


def meter_frame(values, start=T0, unit="kW", states=None, flags=None, in_window=True, date="2019-06-10", day_h=24.0):
    n = len(values)
    starts = pd.date_range(start, periods=n, freq="15min")
    return pd.DataFrame({
        "interval_start_utc": starts, "interval_end_utc": starts + pd.Timedelta("15min"),
        "local_reporting_date": date, "local_day_length_h": day_h,
        "resolved_observation_id": [f"m{i}" for i in range(n)],
        "state": states or ["OBSERVED_ZERO" if v == 0 else "OBSERVED_VALID" for v in values],
        "value": values, "unit": unit, "flags": flags or [""] * n,
        "in_window": in_window, "clearsky_kwh_m2": 0.2,
    })


def poa_frame(values, start=T0, unit="W/m^2", states=None, flags=None):
    n = len(values)
    starts = pd.date_range(start, periods=n, freq="5min")
    return pd.DataFrame({
        "interval_start_utc": starts, "interval_end_utc": starts + pd.Timedelta("5min"),
        "resolved_observation_id": [f"p{i}" for i in range(n)],
        "state": states or ["OBSERVED_ZERO" if v == 0 else "OBSERVED_VALID" for v in values],
        "value": values, "unit": unit, "flags": flags or [""] * n,
    })


def reasons(pairs):
    """Exclusion reasons with nulls normalised to None."""
    return [r if isinstance(r, str) else None for r in pairs["exclusion_reason"]]


def run(meter, poa, capacity=100.0):
    pairs = P.pair_intervals(meter, poa, CFG)
    return pairs, M.daily_metrics(pairs, capacity, CFG).iloc[0]


def test_hand_worked_example_pr_080():
    pairs, day = run(meter_frame([40.0] * 4), poa_frame([500.0] * 12))
    assert pairs["paired"].all()
    assert day["e_ac_kwh"] == pytest.approx(40.0)          # 4 x 40 kW x 0.25 h
    assert day["h_poa_kwh_m2"] == pytest.approx(0.5)       # 12 x 500 W/m2 x 1/12 h / 1000
    assert day["diagnostic_pr"] == pytest.approx(0.8)
    assert M.as_percent(day["diagnostic_pr"]) == pytest.approx(80.0)
    assert day["status"] == "CHECKS_PASSED" and day["reported_pr"] == pytest.approx(0.8)
    assert list(pairs["poa0_observation_id"]) == ["p0", "p3", "p6", "p9"]


def test_missing_one_poa_subinterval_excludes_whole_meter_pair():
    poa = poa_frame([500.0] * 12, states=["OBSERVED_VALID"] * 4 + ["EXPECTED_ROW_MISSING"] + ["OBSERVED_VALID"] * 7)
    poa.loc[4, "value"] = np.nan
    pairs, day = run(meter_frame([40.0] * 4), poa)
    assert reasons(pairs) == [None, "POA_MISSING", None, None]
    assert day["e_ac_kwh"] == pytest.approx(30.0) and day["h_poa_kwh_m2"] == pytest.approx(0.375)
    assert day["diagnostic_pr"] == pytest.approx(0.8)
    assert math.isnan(pairs.loc[1, "e_ac_kwh"]) and math.isnan(pairs.loc[1, "h_poa_kwh_m2"])


def test_missing_meter_excludes_its_irradiation_and_nothing_is_interpolated():
    meter = meter_frame([40.0, np.nan, 40.0, 40.0],
                        states=["OBSERVED_VALID", "EXPECTED_ROW_MISSING", "OBSERVED_VALID", "OBSERVED_VALID"])
    pairs, day = run(meter, poa_frame([500.0] * 12))
    assert pairs.loc[1, "exclusion_reason"] == "METER_MISSING"
    assert day["h_poa_kwh_m2"] == pytest.approx(0.375)    # the 3 POA subintervals of interval 1 are dropped too
    assert day["paired_intervals"] == 3 and day["time_coverage"] == pytest.approx(0.75)
    assert day["status"] == "INSUFFICIENT_DATA" and "LOW_TIME_COVERAGE" in day["reason_codes"]
    assert math.isnan(day["reported_pr"])
    assert pairs["meter_kw_value"].isna().sum() == 1     # no value was filled in


def test_valid_zero_power_gives_zero_diagnostic_pr_and_review():
    flags = ["DAYLIGHT_ZERO_UNRESOLVED:A-DAYZERO"] * 4
    pairs, day = run(meter_frame([0.0] * 4, flags=flags), poa_frame([500.0] * 12))
    assert pairs["paired"].all()
    assert day["diagnostic_pr"] == 0.0
    assert day["status"] == "REVIEW_REQUIRED" and "UNRESOLVED_ZERO_OUTPUT" in day["reason_codes"]
    assert math.isnan(day["reported_pr"])


def test_all_inputs_missing_gives_unavailable_pr():
    meter = meter_frame([np.nan] * 4, states=["EXPECTED_ROW_MISSING"] * 4)
    poa = poa_frame([np.nan] * 12, states=["EXPECTED_ROW_MISSING"] * 12)
    _, day = run(meter, poa)
    assert math.isnan(day["diagnostic_pr"]) and math.isnan(day["reported_pr"])
    assert day["status"] == "INSUFFICIENT_DATA" and day["reason_codes"].startswith("NO_PAIRED_DATA")


def test_zero_and_invalid_denominators_never_give_zero_or_infinity():
    assert math.isnan(M.performance_ratio(10.0, 100.0, 0.0))
    assert math.isnan(M.performance_ratio(10.0, 100.0, -0.1))
    assert math.isnan(M.performance_ratio(10.0, 100.0, float("nan")))
    assert math.isnan(M.performance_ratio(10.0, 100.0, float("inf")))
    _, day = run(meter_frame([40.0] * 4), poa_frame([0.0] * 12))
    assert math.isnan(day["diagnostic_pr"]) and day["status"] == "INSUFFICIENT_DATA"
    assert "LOW_IRRADIATION" in day["reason_codes"] and "PR_UNAVAILABLE" in day["reason_codes"]


@pytest.mark.parametrize("cap", [0.0, -893.0, float("nan"), None])
def test_invalid_or_missing_capacity(cap):
    assert math.isnan(M.performance_ratio(40.0, cap, 0.5))
    _, day = run(meter_frame([40.0] * 4), poa_frame([500.0] * 12), capacity=cap)
    assert day["status"] == "INSUFFICIENT_DATA" and "INVALID_CAPACITY" in day["reason_codes"]
    assert math.isnan(day["diagnostic_pr"])


def test_units_w_vs_kw_and_wm2_vs_kwm2():
    _, a = run(meter_frame([40.0] * 4), poa_frame([500.0] * 12))
    _, b = run(meter_frame([40000.0] * 4, unit="W"), poa_frame([0.5] * 12, unit="kW/m^2"))
    assert b["e_ac_kwh"] == pytest.approx(a["e_ac_kwh"]) and b["h_poa_kwh_m2"] == pytest.approx(a["h_poa_kwh_m2"])
    assert b["diagnostic_pr"] == pytest.approx(0.8)
    with pytest.raises(ValueError):
        run(meter_frame([40.0] * 4, unit="kVA"), poa_frame([500.0] * 12))


def test_duplicate_intervals_are_rejected_and_misaligned_poa_excluded():
    poa = poa_frame([500.0] * 12)
    with pytest.raises(ValueError):
        run(meter_frame([40.0] * 4), pd.concat([poa, poa.iloc[[3]]], ignore_index=True))
    with pytest.raises(ValueError):
        m = meter_frame([40.0] * 4)
        run(pd.concat([m, m.iloc[[0]]], ignore_index=True), poa)
    shifted = poa.copy()
    shifted.loc[4, "interval_end_utc"] += pd.Timedelta("2min")    # overlaps the next subinterval
    pairs, _ = run(meter_frame([40.0] * 4), shifted)
    assert pairs.loc[1, "exclusion_reason"] == "POA_MISALIGNED" and pairs["paired"].sum() == 3


def test_dst_and_local_day_window_rules():
    # 25-hour local day: the DST-ambiguous night intervals are outside the window and cannot affect PR.
    meter = meter_frame([0.0, 0.0, 40.0, 40.0, 40.0, 40.0], date="2019-11-03", day_h=25.0,
                        states=["TIME_AMBIGUOUS", "TIME_AMBIGUOUS"] + ["OBSERVED_VALID"] * 4,
                        flags=["UNRESOLVED_DST_INTERVAL:T-AMBIG"] * 2 + [""] * 4)
    meter["in_window"] = [False, False, True, True, True, True]
    pairs, day = run(meter, poa_frame([500.0] * 18))
    assert list(pairs["exclusion_reason"][:2]) == ["OUTSIDE_WINDOW", "OUTSIDE_WINDOW"]
    assert day["local_day_length_h"] == 25.0 and day["window_intervals"] == 4
    assert day["status"] == "CHECKS_PASSED" and day["diagnostic_pr"] == pytest.approx(0.8)


def test_unresolved_source_timestamp_inside_window_is_excluded_and_reviewed():
    meter = meter_frame([40.0, np.nan, 40.0, 40.0], states=["OBSERVED_VALID", "TIME_AMBIGUOUS"] + ["OBSERVED_VALID"] * 2,
                        flags=["", "UNRESOLVED_DST_INTERVAL:T-AMBIG", "", ""])
    pairs, day = run(meter, poa_frame([500.0] * 12))
    assert pairs.loc[1, "exclusion_reason"] == "METER_TIME_AMBIGUOUS"
    assert "UNRESOLVED_TIME_IN_WINDOW" in day["reason_codes"]


def test_invalid_input_is_a_different_reason_from_zero_output():
    meter = meter_frame([40.0, 1783.68, 0.0, 40.0],
                        states=["OBSERVED_VALID", "INVALID_PHYSICAL", "OBSERVED_ZERO", "OBSERVED_VALID"],
                        flags=["", "INVALID_VALUE:R-LIMITS;SUSPECTED_LOGGER_ARTEFACT:A-CATCHUP",
                               "DAYLIGHT_ZERO_UNRESOLVED:A-DAYZERO", ""])
    pairs, day = run(meter, poa_frame([500.0] * 12))
    assert reasons(pairs) == [None, "METER_INVALID", None, None]
    assert pairs.loc[2, "paired"] and pairs.loc[2, "e_ac_kwh"] == 0.0
    for code in ("INVALID_INPUT_IN_WINDOW", "SUSPECTED_CATCH_UP", "UNRESOLVED_ZERO_OUTPUT"):
        assert code in day["reason_codes"]


def test_status_precedence_and_withheld_reported_pr():
    assert M.resolve_status(["LOW_TIME_COVERAGE"], {"UNRESOLVED_ZERO_OUTPUT"}) == "INSUFFICIENT_DATA"
    assert M.resolve_status([], {"UNRESOLVED_ZERO_OUTPUT"}) == "REVIEW_REQUIRED"
    assert M.resolve_status([], set()) == "CHECKS_PASSED"
    meter = meter_frame([40.0, 0.0, np.nan, 40.0], states=["OBSERVED_VALID", "OBSERVED_ZERO", "EXPECTED_ROW_MISSING",
                                                            "OBSERVED_VALID"],
                        flags=["", "DAYLIGHT_ZERO_UNRESOLVED:A-DAYZERO", "", ""])
    _, day = run(meter, poa_frame([500.0] * 12))
    assert day["status"] == "INSUFFICIENT_DATA"
    assert "LOW_TIME_COVERAGE" in day["reason_codes"] and "UNRESOLVED_ZERO_OUTPUT" in day["reason_codes"]
    assert math.isnan(day["reported_pr"]) and math.isfinite(day["diagnostic_pr"])


def test_context_flag_does_not_withhold():
    poa = poa_frame([500.0] * 12, flags=["SUSPECTED_LOGGER_ARTEFACT:A-CEILING"] + [""] * 11)
    _, day = run(meter_frame([40.0] * 4), poa)
    assert day["status"] == "CHECKS_PASSED" and day["n_context_flags"] == 1


def test_negative_input_kept_and_reviewed():
    poa = poa_frame([500.0] * 11 + [-5.0])
    pairs, day = run(meter_frame([40.0] * 4), poa)
    assert pairs["paired"].all() and "NEGATIVE_INPUT_IN_WINDOW" in day["reason_codes"]
    assert day["h_poa_kwh_m2"] == pytest.approx((11 * 500 - 5) / 12 / 1000)


def test_percentage_point_vs_relative_change():
    assert M.percentage_point_difference(0.76, 0.80) == pytest.approx(-4.0)
    assert M.relative_change_percent(0.76, 0.80) == pytest.approx(-5.0)
    assert math.isnan(M.relative_change_percent(0.76, 0.0))
    assert math.isnan(M.percentage_point_difference(float("nan"), 0.8))
