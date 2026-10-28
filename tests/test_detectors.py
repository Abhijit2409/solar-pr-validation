"""Phase 5 detector tests on small deterministic synthetic days (no real data, no evaluation cases).

Fixture: a clear June day at the site. POA readings are the 5-min means of a 1-min clear-sky
model (so by construction the timing lag is 0 and there are no stale runs), and the meter
reads 0.8 x 893 kW x POA/1000 (so PR is exactly 0.8). Night readings are zero.
"""
import inspect
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import checks_improved as I
import detect as D
import evidence as E
import ingest

ROOT = Path(__file__).resolve().parents[1]
MCFG = yaml.safe_load((ROOT / "config/metric_definition.yaml").read_text())
EXP = yaml.safe_load((ROOT / "config/experiment_v2.yaml").read_text())
META = E.measurement_meta(ingest.load_config())
SITE = {"latitude": 38.996306, "longitude": -122.134111, "elevation_m": 10.0, "tilt_deg": 25.0,
        "azimuth_deg": 180.0, "dc_capacity_kw": 893.0, "local_timezone": "America/Los_Angeles"}
DAY = "2019-06-15"
_CSM = {}


def csm():
    if "m" not in _CSM:
        t0 = pd.Timestamp("2019-06-14 12:00", tz="UTC")
        _CSM["m"] = I.ClearSkyMinutes(SITE, t0, t0 + pd.Timedelta("48h"))
    return _CSM["m"]


def make_obs(natural_lag=0):
    """Detector input for DAY. natural_lag shifts the measured POA relative to the model."""
    start = pd.Timestamp(DAY).tz_localize(SITE["local_timezone"]).tz_convert("UTC")
    poa_t = pd.date_range(start, start + pd.Timedelta("24h"), freq="5min", inclusive="left")
    poa_v = np.round(csm().mean5(csm().index(poa_t) + natural_lag), 3)
    m_t = pd.date_range(start, start + pd.Timedelta("24h"), freq="15min", inclusive="left")
    m_v = np.round(0.8 * 893.0 * poa_v.reshape(-1, 3).mean(axis=1) / 1000, 6)
    df = pd.concat([pd.DataFrame({"signal": "poa_irradiance", "interval_start_utc": poa_t, "value": poa_v,
                                  "unit": "W/m^2"}),
                    pd.DataFrame({"signal": "ac_power", "interval_start_utc": m_t, "value": m_v, "unit": "kW"})],
                   ignore_index=True)
    df.insert(0, "obs_ref", [f"r{i:07d}" for i in range(len(df))])
    return df


def run(obs, baseline=0, methods=("basic", "improved")):
    checks, days, ev = D.evaluate(obs, SITE, META, MCFG, EXP, baseline, [DAY], methods)
    return checks, days.set_index("method"), ev


def check(checks, method, cid):
    return checks[(checks["method"] == method) & (checks["check_id"] == cid)].iloc[0]


def window_times(obs, signal="poa_irradiance"):
    _, _, ev = run(obs, methods=("basic",))
    frame = ev["poa"] if signal == "poa_irradiance" else ev["meter"]
    return frame.loc[frame["in_window"].fillna(False).astype(bool), "interval_start_utc"].sort_values().tolist()


def noon_index(times):
    return int(np.argmin([abs((t.tz_convert(SITE["local_timezone"]).hour * 60 + t.minute) - 13 * 60) for t in times]))


def at(obs, signal, t):
    return obs.index[(obs["signal"] == signal) & (obs["interval_start_utc"] == t)][0]


# ---------- baseline fixture ----------
def test_clean_day_passes_both_methods_with_pr_080_and_night_zeros_ignored():
    checks, days, _ = run(make_obs())
    for m in ("basic", "improved"):
        assert days.loc[m, "status"] == "CHECKS_PASSED", days.loc[m, "reason_codes"]
        assert days.loc[m, "diagnostic_pr"] == pytest.approx(0.8, abs=1e-9)
        assert days.loc[m, "reported_pr"] == pytest.approx(0.8, abs=1e-9)
    assert check(checks, "improved", "I3")["outcome"] == "PASS"
    assert check(checks, "improved", "I3")["measured"] == 0
    for cid in ("B5b", "B5c", "B5d"):                          # night zeros are outside the window
        assert check(checks, "basic", cid)["outcome"] == "PASS"
    assert "basic method" in days.loc["basic", "limitations"] and days.loc["improved", "limitations"] == ""


# ---------- B1 time coverage boundary ----------
def test_time_coverage_boundary():
    obs = make_obs()
    mt = window_times(obs, "ac_power")
    n = len(mt)
    k_ok = math.floor(0.10 * n)                                # coverage (n-k)/n >= 0.90
    for k, expected in ((k_ok, "PASS"), (k_ok + 1, "FLAGGED")):
        o = obs.drop([at(obs, "ac_power", t) for t in mt[:k]])
        checks, days, _ = run(o, methods=("basic",))
        assert check(checks, "basic", "B1")["outcome"] == expected
        assert check(checks, "basic", "B1")["measured"] == pytest.approx((n - k) / n, abs=1e-6)


# ---------- B2 range and negatives ----------
def test_invalid_and_negative_values_in_window():
    obs = make_obs()
    t = window_times(obs, "ac_power")[20]
    o = obs.copy()
    o.loc[at(o, "ac_power", t), "value"] = 900.0               # above the 893 kW screening bound
    checks, days, ev = run(o, methods=("basic",))
    assert check(checks, "basic", "B2")["outcome"] == "FLAGGED"
    assert days.loc["basic", "status"] == "REVIEW_REQUIRED"
    assert ev["pairs"].set_index("interval_start_utc").loc[t, "exclusion_reason"] == "METER_INVALID"
    o = obs.copy()
    o.loc[at(o, "poa_irradiance", window_times(obs)[40]), "value"] = -5.0  # negative but within bounds
    checks, _, _ = run(o, methods=("basic",))
    assert check(checks, "basic", "B2b")["outcome"] == "FLAGGED" and check(checks, "basic", "B2")["outcome"] == "PASS"


# ---------- B3 duplicates, B4 off-grid ----------
def test_duplicate_and_conflicting_rows_in_window():
    obs = make_obs()
    t = window_times(obs, "ac_power")[30]
    row = obs.loc[[at(obs, "ac_power", t)]].assign(obs_ref="x0000001")
    checks, _, ev = run(pd.concat([obs, row], ignore_index=True), methods=("basic",))
    assert check(checks, "basic", "B3")["outcome"] == "FLAGGED"
    assert ev["pairs"].set_index("interval_start_utc").loc[t, "paired"]       # identical duplicate still pairs
    checks, _, ev = run(pd.concat([obs, row.assign(value=1.0)], ignore_index=True), methods=("basic",))
    assert check(checks, "basic", "B3")["outcome"] == "FLAGGED"
    assert ev["pairs"].set_index("interval_start_utc").loc[t, "exclusion_reason"] == "METER_CONFLICT"


def test_off_grid_reading_in_window():
    obs = make_obs()
    t = window_times(obs, "ac_power")[30] + pd.Timedelta("7min")
    extra = pd.DataFrame({"obs_ref": ["x0000002"], "signal": ["ac_power"], "interval_start_utc": [t],
                          "value": [100.0], "unit": ["kW"]})
    checks, days, _ = run(pd.concat([obs, extra], ignore_index=True), methods=("basic",))
    assert check(checks, "basic", "B4")["outcome"] == "FLAGGED" and days.loc["basic", "status"] == "REVIEW_REQUIRED"


# ---------- B5 zero-output policy and catch-up ----------
def poa_mean(obs, t):
    p = obs[(obs["signal"] == "poa_irradiance") & (obs["interval_start_utc"] >= t)
            & (obs["interval_start_utc"] < t + pd.Timedelta("15min"))]
    return p["value"].mean()


def set_poa_interval(obs, t, value):
    for k in (0, 5, 10):
        obs.loc[at(obs, "poa_irradiance", t + pd.Timedelta(minutes=k)), "value"] = value


def test_suspicious_zero_threshold_boundary():
    for poa, expected in ((200.0, "FLAGGED"), (199.9, "PASS")):
        o = make_obs()
        t = window_times(o, "ac_power")[25]
        set_poa_interval(o, t, poa)
        o.loc[at(o, "ac_power", t), "value"] = 0.0
        checks, _, _ = run(o, methods=("basic",))
        assert check(checks, "basic", "B5b")["outcome"] == expected


def test_material_zero_share_boundary():
    obs = make_obs()
    mt = window_times(obs, "ac_power")
    h = {t: poa_mean(obs, t) * 0.25 / 1000 for t in mt}
    total = sum(h.values())
    low = [t for t in mt if poa_mean(obs, t) < 199]            # zeros here are never "suspicious"
    chosen, share = [], 0.0
    for t in low:                                             # smallest set reaching >= 1%
        chosen.append(t)
        share += h[t] / total
        if share >= 0.01:
            break
    for subset, expected in ((chosen[:-1], "PASS"), (chosen, "FLAGGED")):
        o = obs.copy()
        for t in subset:
            o.loc[at(o, "ac_power", t), "value"] = 0.0
        checks, _, _ = run(o, methods=("basic",))
        r = check(checks, "basic", "B5c")
        assert r["outcome"] == expected
        assert r["measured"] == pytest.approx(sum(h[t] for t in subset) / total, abs=1e-6)
        assert check(checks, "basic", "B5b")["outcome"] == "PASS"


def test_suspicious_poa_zero_and_night_zero():
    o = make_obs()
    o.loc[at(o, "poa_irradiance", window_times(o)[noon_index(window_times(o))]), "value"] = 0.0
    checks, _, _ = run(o, methods=("basic",))
    assert check(checks, "basic", "B5d")["outcome"] == "FLAGGED"


def test_catch_up_recomputed_from_values():
    o = make_obs()
    mt = window_times(o, "ac_power")
    i = noon_index(mt)
    for t in mt[i - 2:i]:
        o.loc[at(o, "ac_power", t), "value"] = 0.0
    o.loc[at(o, "ac_power", mt[i]), "value"] = 1783.68
    checks, days, _ = run(o, methods=("basic",))
    r = check(checks, "basic", "B5a")
    assert r["outcome"] == "FLAGGED" and r["n_affected_intervals"] == 3
    assert {"SUSPECTED_CATCH_UP", "INVALID_INPUT_IN_WINDOW"} <= set(days.loc["basic", "reason_codes"].split(";"))


# ---------- missing data: midday vs edge, removed row vs explicit null ----------
def test_midday_block_vs_edge_block_of_equal_length():
    obs = make_obs()
    pt = window_times(obs)
    i = noon_index(pt)
    i -= i % 3
    mid, edge = pt[i - 6:i + 6], pt[:12]                       # 60 min each, aligned to meter intervals
    res = {}
    for name, block in (("mid", mid), ("edge", edge)):
        _, days, _ = run(obs.drop([at(obs, "poa_irradiance", t) for t in block]))
        res[name] = days
    assert res["mid"].loc["basic", "time_coverage"] == pytest.approx(res["edge"].loc["basic", "time_coverage"])
    assert res["mid"].loc["basic", "status"] == "CHECKS_PASSED"
    assert res["mid"].loc["improved", "status"] == "INSUFFICIENT_DATA"
    assert "LOW_CLEARSKY_WEIGHTED_COVERAGE" in res["mid"].loc["improved", "reason_codes"]
    assert res["edge"].loc["improved", "status"] == "CHECKS_PASSED"
    assert res["mid"].loc["improved", "clearsky_weighted_coverage"] < res["edge"].loc["improved",
                                                                                      "clearsky_weighted_coverage"]


def test_removed_row_versus_explicit_null():
    obs = make_obs()
    t = window_times(obs)[60]
    removed = obs.drop(at(obs, "poa_irradiance", t))
    nulled = obs.copy()
    nulled.loc[at(nulled, "poa_irradiance", t), "value"] = np.nan
    _, d1, e1 = run(removed, methods=("basic",))
    _, d2, e2 = run(nulled, methods=("basic",))
    mt = t.floor("15min")
    assert e1["pairs"].set_index("interval_start_utc").loc[mt, "exclusion_reason"] == "POA_MISSING"
    assert e2["pairs"].set_index("interval_start_utc").loc[mt, "exclusion_reason"] == "POA_SOURCE_NULL"
    assert d1.loc["basic", "time_coverage"] == d2.loc["basic", "time_coverage"]


# ---------- stale values ----------
def hold_last(obs, start_idx, n):
    o = obs.copy()
    pt = window_times(obs)
    held = o.loc[at(o, "poa_irradiance", pt[start_idx - 1]), "value"]
    for t in pt[start_idx:start_idx + n]:
        o.loc[at(o, "poa_irradiance", t), "value"] = held
    return o, held


@pytest.mark.parametrize("n_held,readings,expected", [(4, 5, "PASS"), (5, 6, "FLAGGED"), (6, 7, "FLAGGED")])
def test_flatline_below_at_and_above_min_run(n_held, readings, expected):
    pt = window_times(make_obs())
    o, held = hold_last(make_obs(), noon_index(pt), n_held)
    checks, days, _ = run(o)
    r = check(checks, "improved", "I2")
    assert r["measured"] == readings and r["outcome"] == expected
    assert check(checks, "basic", "B2")["outcome"] == "PASS"    # plausible in-range value
    assert 500 < held < 1400
    if expected == "FLAGGED":
        assert json.loads(r["evidence"])["stale_runs"][0]["span_min"] == 5 * readings


def test_stale_runs_break_at_gaps_and_invalid_readings():
    pt = window_times(make_obs())
    i = noon_index(pt)
    o, _ = hold_last(make_obs(), i, 8)                          # 9 identical readings
    gap = o.drop(at(o, "poa_irradiance", pt[i + 4]))
    checks, _, _ = run(gap)
    assert check(checks, "improved", "I2")["measured"] == 5 and check(checks, "improved", "I2")["outcome"] == "PASS"
    bad = o.copy()
    bad.loc[at(bad, "poa_irradiance", pt[i + 4]), "value"] = 5000.0
    checks, _, _ = run(bad)
    assert check(checks, "improved", "I2")["outcome"] == "PASS"


# ---------- timing ----------
@pytest.mark.parametrize("shift", [-60, -30, -15, 15, 30, 60])
def test_known_clock_shift_moves_lag_by_minus_shift(shift):
    obs = make_obs()
    o = obs.copy()
    poa = o["signal"] == "poa_irradiance"
    o.loc[poa, "interval_start_utc"] = o.loc[poa, "interval_start_utc"] + pd.Timedelta(minutes=shift)
    checks, days, _ = run(o, baseline=0)
    r = check(checks, "improved", "I3")
    assert r["measured"] == -shift and r["outcome"] == "FLAGGED"
    assert days.loc["improved", "status"] == "REVIEW_REQUIRED"
    assert days.loc["basic", "status"] in ("CHECKS_PASSED", "REVIEW_REQUIRED", "INSUFFICIENT_DATA")


def test_baseline_comparison_uses_development_baseline_without_correction():
    obs = make_obs(natural_lag=-11)                             # site-like discrepancy
    checks, _, _ = run(obs, baseline=-11)
    r = check(checks, "improved", "I3")
    assert r["measured"] == -11 and r["outcome"] == "PASS"
    poa = obs["signal"] == "poa_irradiance"
    shifted = obs.copy()
    shifted.loc[poa, "interval_start_utc"] += pd.Timedelta("15min")
    checks, _, _ = run(shifted, baseline=-11)
    r = check(checks, "improved", "I3")
    assert r["measured"] == -26 and json.loads(r["evidence"])["lag_minus_baseline_min"] == -15
    small = obs.copy()
    small.loc[poa, "interval_start_utc"] += pd.Timedelta("5min")  # |-16 - (-11)| = 5 < 8
    assert check(run(small, baseline=-11)[0], "improved", "I3")["outcome"] == "PASS"


def test_cloudy_constant_boundary_and_sparse_timing_are_inconclusive():
    rng = np.random.default_rng(7)
    obs = make_obs()
    poa = obs["signal"] == "poa_irradiance"
    cloudy = obs.copy()
    cloudy.loc[poa, "value"] = cloudy.loc[poa, "value"] * rng.uniform(0.2, 1.0, poa.sum())
    const = obs.copy()
    const.loc[poa & (const["value"] > 0), "value"] = 500.0
    far = obs.copy()
    far.loc[poa, "interval_start_utc"] += pd.Timedelta("150min")
    keep_every_4th = obs.index[poa][::4]
    sparse = obs.drop(obs.index[poa].difference(keep_every_4th))   # ~44 eligible readings < 60
    for o, why in ((cloudy, "not a clear day"), (const, "constant"), (far, "boundary"),
                   (sparse, "fewer than")):
        checks, days, _ = run(o)
        r = check(checks, "improved", "I3")
        assert r["outcome"] == "INCONCLUSIVE" and r["action"] == "CONTEXT", why
        assert why in json.loads(r["evidence"])["inconclusive_reason"]


def test_best_lag_tie_rule():
    lags = np.array([-2, -1, 0, 1, 2])
    assert I.best_lag(np.array([0.5, 0.9, 0.9, 0.9, 0.2]), lags)[0] == 0
    assert I.best_lag(np.array([0.5, 0.9, 0.1, 0.9, 0.2]), lags)[0] == -1
    assert I.best_lag(np.array([np.nan] * 5), lags)[0] is None


def test_passed_day_with_inconclusive_timing_states_limitation():
    rng = np.random.default_rng(3)
    obs = make_obs()
    poa = obs["signal"] == "poa_irradiance"
    o = obs.copy()
    f = rng.uniform(0.6, 1.0, poa.sum())
    o.loc[poa, "value"] = o.loc[poa, "value"] * f
    mp = o["signal"] == "ac_power"
    o.loc[mp, "value"] = 0.8 * 893 * o.loc[poa, "value"].to_numpy().reshape(-1, 3).mean(axis=1) / 1000
    _, days, _ = run(o)
    assert days.loc["improved", "status"] == "CHECKS_PASSED"
    assert "TIMING_INCONCLUSIVE" in days.loc["improved", "context_codes"]
    assert "timing not verified" in days.loc["improved", "limitations"]


# ---------- precedence, inheritance, reported PR ----------
def test_combined_reasons_precedence_and_inheritance():
    obs = make_obs()
    pt = window_times(obs)
    i = noon_index(pt)
    o = obs.drop([at(obs, "poa_irradiance", t) for t in pt[i - 24:i + 24]])     # 240 min midday gap
    mt = window_times(obs, "ac_power")
    o.loc[at(o, "ac_power", mt[5]), "value"] = 900.0                              # invalid -> review
    checks, days, _ = run(o)
    for m in ("basic", "improved"):
        assert days.loc[m, "status"] == "INSUFFICIENT_DATA"
        assert "LOW_TIME_COVERAGE" in days.loc[m, "reason_codes"]
        assert "INVALID_INPUT_IN_WINDOW" in days.loc[m, "reason_codes"]
        assert math.isnan(days.loc[m, "reported_pr"]) and math.isfinite(days.loc[m, "diagnostic_pr"])
    assert set(days.loc["basic", "reason_codes"].split(";")) <= set(days.loc["improved", "reason_codes"].split(";"))
    basic_ids = set(checks.loc[checks["method"] == "basic", "check_id"])
    assert basic_ids <= set(checks.loc[checks["method"] == "improved", "check_id"])
    assert set(checks.loc[checks["method"] == "improved", "check_id"]) - basic_ids == {"I1", "I2", "I3"}


def test_improved_never_passes_a_day_basic_withholds():
    o = make_obs()
    o.loc[at(o, "ac_power", window_times(o, "ac_power")[10]), "value"] = 900.0
    _, days, _ = run(o)
    assert days.loc["basic", "status"] == "REVIEW_REQUIRED" and days.loc["improved", "status"] != "CHECKS_PASSED"


# ---------- no injection truth reaches the detector ----------
def test_detector_input_contract_has_no_truth():
    obs = make_obs()
    with pytest.raises(ValueError):
        E.validate_input(obs.assign(fault_label="flatline"))
    with pytest.raises(ValueError):
        E.validate_input(obs.assign(source_file="x.csv"))
    params = set(inspect.signature(D.evaluate).parameters)
    assert params == {"obs", "site", "meta", "metric_cfg", "exp_cfg", "timing_baseline", "dates", "methods"}
    forbidden = {"label", "scenario", "severity", "expected", "fault", "window_list", "case"}
    assert not any(f in p for p in params for f in forbidden)
    assert list(obs.columns) == E.DETECTOR_INPUT_COLUMNS
