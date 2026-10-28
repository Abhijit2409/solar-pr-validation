"""Injector tests on synthetic observations (no real or evaluation data)."""
import numpy as np
import pandas as pd
import pytest

import evidence as E
import inject_faults as INJ
from synthetic import DAY, EXP3, MCFG, META, SITE, days_around, make_obs_days

TZ = SITE["local_timezone"]


def obs5():
    return make_obs_days(days_around(DAY))


def plan(family, window_id="w-test-01"):
    return INJ.plan_window(window_id, family, DAY, SITE, MCFG, EXP3)


def frame_hash(f):
    return pd.util.hash_pandas_object(f, index=True).sum()


def poa_count(f):
    return int((f["signal"] == "poa_irradiance").sum())


def local_date(ts):
    return ts.dt.tz_convert(TZ).dt.strftime("%Y-%m-%d")


# ---------- missing daytime ----------
@pytest.mark.parametrize("minutes,rows", [(30, 6), (60, 12), (120, 24), (240, 48)])
def test_removed_rows_exact_counts_and_grid_edges(minutes, rows):
    o = obs5()
    p = plan("missing_daytime")
    f, t = INJ.apply_case(o, p, "primary:rows_removed", minutes, SITE, META, EXP3)
    assert t["injection_status"] == "APPLIED" and t["n_rows_affected"] == rows == t["n_values_changed"]
    assert poa_count(o) - poa_count(f) == rows
    assert (f["signal"] == "ac_power").sum() == (o["signal"] == "ac_power").sum()
    assert pd.Timestamp(t["block_start_utc"]).minute % 15 == 0 and pd.Timestamp(t["block_end_utc"]).minute % 15 == 0
    assert set(t["affected_refs"].split(";")).isdisjoint(set(f["obs_ref"]))


def test_missing_blocks_nested_and_centre_fits_largest_block():
    o, p = obs5(), plan("missing_daytime")
    refs = [set(INJ.apply_case(o, p, "primary:rows_removed", m, SITE, META, EXP3)[1]["affected_refs"].split(";"))
            for m in (30, 60, 120, 240)]
    assert refs[0] < refs[1] < refs[2] < refs[3]
    g = INJ.day_geometry(DAY, SITE, MCFG)
    win = set(g.loc[g["in_window"], "interval_start_utc"])
    c = p["placement"]
    assert all(c + pd.Timedelta(minutes=15 * k) in win for k in range(-8, 8))


def test_explicit_nulls_keep_rows_removed_rows_disappear():
    o, p = obs5(), plan("missing_daytime")
    f, t = INJ.apply_case(o, p, "secondary:explicit_nulls", 120, SITE, META, EXP3)
    assert len(f) == len(o) and t["n_values_changed"] == 24
    assert f.set_index("obs_ref").loc[t["affected_refs"].split(";"), "value"].isna().all()
    g, _ = INJ.apply_case(o, p, "primary:rows_removed", 120, SITE, META, EXP3)
    assert len(o) - len(g) == 24


# ---------- timestamp shifts ----------
SHIFT_CASES = [("primary:poa_only", s) for s in (-60, -30, -15, 15, 30, 60)] + \
              [("secondary:whole_file", s) for s in (-60, -30, 30, 60)]


@pytest.mark.parametrize("scenario,shift", SHIFT_CASES)
def test_shifts_move_block_without_changing_values(scenario, shift):
    assert (scenario, shift) in INJ.case_specs("timestamp_shift", EXP3)
    o, p = obs5(), plan("timestamp_shift")
    f, t = INJ.apply_case(o, p, scenario, shift, SITE, META, EXP3)
    block = {(pd.Timestamp(DAY) + pd.Timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)}
    shifted_signals = ["poa_irradiance"] if scenario.startswith("primary") else ["poa_irradiance", "ac_power"]
    for sig in ("poa_irradiance", "ac_power"):
        a = o[o["signal"] == sig].set_index("obs_ref")
        b = f[f["signal"] == sig].set_index("obs_ref").loc[a.index]
        assert (a["value"].to_numpy() == b["value"].to_numpy()).all()       # values preserved
        in_block = local_date(a["interval_start_utc"]).isin(block)
        delta = (b["interval_start_utc"] - a["interval_start_utc"]).dt.total_seconds() / 60
        expected = np.where(in_block & (sig in shifted_signals), shift, 0)
        assert (delta.to_numpy() == expected).all()
    exp_coll = abs(shift) // 5 + (abs(shift) // 15 if scenario.startswith("secondary") else 0)
    assert t["n_collisions"] == exp_coll                                   # rows pushed onto D-2 or D+2


def test_shift_day_boundary_moves_rows_into_evaluated_day():
    o, p = obs5(), plan("timestamp_shift")
    f, _ = INJ.apply_case(o, p, "primary:poa_only", 60, SITE, META, EXP3)
    orig = o.set_index("obs_ref")
    poa = f[f["signal"] == "poa_irradiance"].set_index("obs_ref")
    moved_in = (local_date(poa["interval_start_utc"]) == DAY) & \
               (local_date(orig.loc[poa.index, "interval_start_utc"]) != DAY)
    assert int(moved_in.sum()) == 12                                      # the last hour of D-1


# ---------- flatlines ----------
def test_hold_last_uses_last_usable_pre_block_value():
    o, p = obs5(), plan("flatline_poa")
    t0 = p["placement"]
    poa = o[o["signal"] == "poa_irradiance"].set_index("interval_start_utc")
    f, t = INJ.apply_case(o, p, "primary:hold_last", 60, SITE, META, EXP3)
    assert t["hold_value"] == poa.loc[t0 - pd.Timedelta("5min"), "value"]
    blk = f[(f["signal"] == "poa_irradiance") & (f["interval_start_utc"] >= t0)
            & (f["interval_start_utc"] < t0 + pd.Timedelta("60min"))]
    assert len(blk) == 12 and (blk["value"] == t["hold_value"]).all()
    o2 = o.copy()
    o2.loc[(o2["signal"] == "poa_irradiance") & (o2["interval_start_utc"] == t0 - pd.Timedelta("5min")), "value"] = np.nan
    _, t2 = INJ.apply_case(o2, p, "primary:hold_last", 60, SITE, META, EXP3)
    assert t2["hold_value"] == poa.loc[t0 - pd.Timedelta("10min"), "value"]  # skips the unusable reading


def test_hold_last_without_usable_pre_block_value_is_unscorable():
    o, p = obs5(), plan("flatline_poa")
    t0 = p["placement"]
    o2 = o.copy()
    poa = o2["signal"] == "poa_irradiance"
    on_day = local_date(o2["interval_start_utc"]) == DAY
    o2.loc[poa & on_day & (o2["interval_start_utc"] < t0), "value"] = np.nan
    _, t = INJ.apply_case(o2, p, "primary:hold_last", 60, SITE, META, EXP3)
    assert t["injection_status"] == "UNSCORABLE" and t["unscorable_reason"] == "no_hold_value"


def test_fixed_value_flatline():
    o, p = obs5(), plan("flatline_poa")
    f, t = INJ.apply_case(o, p, "secondary:fixed_value_600", 120, SITE, META, EXP3)
    blk = f.set_index("obs_ref").loc[t["affected_refs"].split(";")]
    assert len(blk) == 24 and (blk["value"] == 600.0).all() and t["n_values_changed"] == 24


def test_no_placement_and_no_effect_are_unscorable():
    o = obs5()
    p = dict(plan("missing_daytime"), placement=None)
    _, t = INJ.apply_case(o, p, "primary:rows_removed", 60, SITE, META, EXP3)
    assert t["injection_status"] == "UNSCORABLE" and t["unscorable_reason"] == "no_placement"
    g = INJ.day_geometry(DAY, SITE, MCFG).assign(in_window=False)          # no window at all
    assert INJ.missing_centre(g, np.random.default_rng(0))[0] is None
    flat = o.copy()
    flat.loc[flat["signal"] == "poa_irradiance", "value"] = 500.0          # already constant
    _, t = INJ.apply_case(flat, plan("flatline_poa"), "primary:hold_last", 60, SITE, META, EXP3)
    assert t["injection_status"] == "UNSCORABLE" and t["unscorable_reason"] == "no_effect"
    no_poa = o[o["signal"] == "ac_power"]
    _, t = INJ.apply_case(no_poa, plan("missing_daytime"), "primary:rows_removed", 60, SITE, META, EXP3)
    assert t["unscorable_reason"] == "no_effect"


# ---------- immutability, determinism, refs ----------
def test_input_frame_never_mutated():
    o = obs5()
    before = frame_hash(o)
    for fam in ("missing_daytime", "timestamp_shift", "flatline_poa"):
        p = plan(fam)
        for scen, sev in INJ.case_specs(fam, EXP3):
            INJ.apply_case(o, p, scen, sev, SITE, META, EXP3)
    assert frame_hash(o) == before


def test_placement_repeatable_and_order_independent():
    ids = ["w-a", "w-b", "w-c"]
    fwd = {w: plan("flatline_poa", w)["placement"] for w in ids}
    rev = {w: plan("flatline_poa", w)["placement"] for w in reversed(ids)}
    assert fwd == rev
    assert plan("missing_daytime", "w-a")["placement"] == plan("missing_daytime", "w-a")["placement"]
    assert INJ.window_hash("w-a") == INJ.window_hash("w-a") != INJ.window_hash("w-b")


def test_reassigned_refs_are_opaque_and_uniform():
    o, p = obs5(), plan("flatline_poa")
    f, t = INJ.apply_case(o, p, "primary:hold_last", 120, SITE, META, EXP3)
    d0, _ = INJ.reassign_refs(o)
    d1, ref_map = INJ.reassign_refs(f)
    for d in (d0, d1):
        E.validate_input(d)
        assert d["obs_ref"].str.fullmatch(r"r\d{7}").all()
    assert set(ref_map["previous_ref"]) == set(f["obs_ref"])
    assert not any(w in " ".join(d1.columns) for w in ("fault", "case", "severity", "scenario"))
