import pandas as pd

from conftest import base_config, fall_back_rows, local_rows, run_tables, spring_rows

DAY = "2019-06-10"


def summer_frames():
    meter = local_rows(f"{DAY} 00:00", f"{DAY} 23:59", 15, value=300.0, drop=["2019-06-10 10:00:00"])
    meter = meter.set_index("timestamp_original")
    meter.loc["2019-06-10 12:00:00", "raw_text"] = "0.0"         # daylight zero
    meter.loc["2019-06-10 12:15:00", "raw_text"] = ""            # source null
    meter.loc["2019-06-10 12:30:00", "raw_text"] = "2000.0"      # above DC nameplate -> invalid
    meter = meter.reset_index()
    dup_identical = meter[meter["timestamp_original"] == "2019-06-10 13:00:00"]
    dup_conflict = meter[meter["timestamp_original"] == "2019-06-10 14:00:00"].assign(raw_text="999.0")
    meter = pd.concat([meter, dup_identical, dup_conflict], ignore_index=True)
    poa = local_rows(f"{DAY} 00:00", f"{DAY} 23:59", 5, value=800.0)
    inc = pd.DataFrame({"timestamp_original": ["2019-06-10 15:00:00"], "raw_text": ["123.0"]})
    return {"meter_main": meter, "poa_main": poa, "meter_2024": inc}


def tables():
    cfg = base_config(f"{DAY} 00:00:00", "2019-06-11 00:00:00")
    return run_tables(cfg, summer_frames())


def obs_at(obs, sensor, local_text):
    return obs[(obs["sensor_id"] == sensor) & (obs["timestamp_original"] == local_text)]


def test_missing_row_is_generated_and_distinct_from_null():
    obs, ei, flags, _ = tables()
    missing = obs[(obs["observation_state"] == "EXPECTED_ROW_MISSING") & (obs["sensor_id"] == "meter_149578")]
    assert len(missing) == 1
    assert missing["source_row"].isna().all() and missing["raw_value"].isna().all()
    assert missing["interval_start_utc"].iloc[0] == pd.Timestamp("2019-06-10 17:00", tz="UTC")
    null = obs_at(obs, "meter_149578", "2019-06-10 12:15:00")
    assert list(null["observation_state"]) == ["SOURCE_NULL"]
    assert null["source_row"].notna().all()
    assert (flags["flag_type"] == "MISSING_EXPECTED_INTERVAL").sum() == 1


def test_zero_invalid_and_valid_states():
    obs, _, flags, _ = tables()
    zero = obs_at(obs, "meter_149578", "2019-06-10 12:00:00")
    assert list(zero["observation_state"]) == ["OBSERVED_ZERO"]
    assert "DAYLIGHT_ZERO_UNRESOLVED" in zero["quality_notes"].iloc[0]
    invalid = obs_at(obs, "meter_149578", "2019-06-10 12:30:00")
    assert list(invalid["observation_state"]) == ["INVALID_PHYSICAL"]
    assert invalid["raw_value"].iloc[0] == "2000.0"
    assert list(obs_at(obs, "meter_149578", "2019-06-10 09:00:00")["observation_state"]) == ["OBSERVED_VALID"]


def test_identical_duplicate_keeps_first_and_marks_second():
    obs, ei, flags, _ = tables()
    rows = obs_at(obs, "meter_149578", "2019-06-10 13:00:00").sort_values("source_row")
    assert list(rows["observation_state"]) == ["OBSERVED_VALID", "DUPLICATE_IDENTICAL"]
    assert (flags["flag_type"] == "DUPLICATE_TIMESTAMP_IDENTICAL").sum() == 1


def test_conflicting_duplicates_are_retained_and_unresolved():
    obs, ei, flags, _ = tables()
    rows = obs_at(obs, "meter_149578", "2019-06-10 14:00:00")
    assert len(rows) == 2 and set(rows["observation_state"]) == {"DUPLICATE_CONFLICT"}
    assert set(rows["raw_value"]) == {"300.0", "999.0"}
    interval = ei[(ei["sensor_id"] == "meter_149578") &
                  (ei["interval_start_utc"] == pd.Timestamp("2019-06-10 21:00", tz="UTC"))]
    assert list(interval["coverage_status"]) == ["CONFLICT_UNRESOLVED"]


def test_cross_file_conflict_is_flagged_and_lower_rank_excluded():
    obs, _, flags, _ = tables()
    rows = obs_at(obs, "meter_149578", "2019-06-10 15:00:00")
    inc = rows[rows["source_id"] == "meter_2024"]
    assert list(inc["observation_state"]) == ["EXCLUDED_BY_SCOPE"]
    assert list(inc["precedence_role"]) == ["superseded_conflict"]
    assert list(rows.loc[rows["source_id"] == "meter_main", "observation_state"]) == ["OBSERVED_VALID"]
    assert (flags["flag_type"] == "CONFLICTING_OVERLAPPING_SOURCE").sum() == 1


def test_units_and_lineage():
    obs, _, _, _ = tables()
    src = obs[obs["source_row"].notna()]
    num = pd.to_numeric(src["raw_value"].replace("", None), errors="coerce")
    assert ((src["standardized_value"] - num).abs().fillna(0) == 0).all()
    assert set(src.loc[src["sensor_id"] == "meter_149578", "conversion_rule"]) == {"kW = kW * 1 + 0"}
    assert set(src.loc[src["sensor_id"] == "poa_149574", "conversion_rule"]) == {"W/m^2 = W/m^2 * 1 + 0"}
    assert src["source_file"].notna().all() and src["source_hash"].notna().all()
    assert not src["observation_id"].duplicated().any()


def test_row_reconciliation():
    obs, ei, _, _ = tables()
    frames = summer_frames()
    n_source = sum(len(f) for f in frames.values())
    n_missing = (obs["observation_state"] == "EXPECTED_ROW_MISSING").sum()
    assert len(obs) == n_source + n_missing
    assert (ei["coverage_status"] == "MISSING").sum() == n_missing


def test_november_fall_back_day_in_pipeline():
    day = "2019-11-03"
    cfg = base_config(f"{day} 00:00:00", "2019-11-04 00:00:00")
    obs, ei, flags, _ = run_tables(cfg, {"meter_main": fall_back_rows(day, 15),
                                         "poa_main": fall_back_rows(day, 5)})
    meter_ei = ei[ei["sensor_id"] == "meter_149578"]
    assert len(meter_ei) == 100                                # 25-hour day
    assert (meter_ei["coverage_status"] == "DST_AMBIGUOUS").sum() == 8
    assert (meter_ei["coverage_status"] == "MISSING").sum() == 0
    amb = obs[(obs["sensor_id"] == "meter_149578") & (obs["observation_state"] == "TIME_AMBIGUOUS")]
    assert len(amb) == 4 and amb["timestamp_utc"].isna().all()
    res = obs[(obs["sensor_id"] == "meter_149578") & obs["timestamp_utc"].notna()].sort_values("source_row")
    assert res["timestamp_utc"].is_monotonic_increasing


def test_november_identical_pair_in_pipeline_keeps_both_raw_rows_unresolved():
    day = "2019-11-03"
    cfg = base_config(f"{day} 00:00:00", "2019-11-04 00:00:00")
    meter = fall_back_rows(day, 15)
    meter.loc[len(meter)] = {"timestamp_original": "2019-11-03 01:15:00", "raw_text": "0.0"}   # second copy
    obs, ei, flags, _ = run_tables(cfg, {"meter_main": meter, "poa_main": fall_back_rows(day, 5)})
    pair = obs[(obs["sensor_id"] == "meter_149578") & (obs["timestamp_original"] == "2019-11-03 01:15:00")]
    assert len(pair) == 2 and pair["source_row"].nunique() == 2
    assert set(pair["observation_state"]) == {"TIME_AMBIGUOUS"} and pair["timestamp_utc"].isna().all()
    assert pair["quality_notes"].str.contains("08:15Z \\(daylight\\) or 2019-11-03T09:15Z").all()
    cand = ei[(ei["sensor_id"] == "meter_149578") & ei["interval_start_utc"].isin(
        [pd.Timestamp("2019-11-03 08:15", tz="UTC"), pd.Timestamp("2019-11-03 09:15", tz="UTC")])]
    assert list(cand["coverage_status"]) == ["DST_AMBIGUOUS", "DST_AMBIGUOUS"]
    assert not (flags["flag_type"] == "DST_PAIR_RESOLVED_BY_ORDER").any()


def test_march_spring_forward_day_in_pipeline():
    day = "2019-03-10"
    cfg = base_config(f"{day} 00:00:00", "2019-03-11 00:00:00")
    obs, ei, flags, _ = run_tables(cfg, {"meter_main": spring_rows(day, 15),
                                         "poa_main": spring_rows(day, 5)})
    meter_ei = ei[ei["sensor_id"] == "meter_149578"]
    assert len(meter_ei) == 92                                 # 23-hour day
    assert (meter_ei["coverage_status"] == "MISSING").sum() == 0
    assert not (obs["observation_state"] == "TIME_AMBIGUOUS").any()
    assert not (flags["flag_type"] == "IRREGULAR_INTERVAL").any()
