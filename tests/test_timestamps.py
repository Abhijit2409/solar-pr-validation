import pandas as pd

import timestamps as T

TZ = "America/Los_Angeles"


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def test_normal_summer_and_winter_days():
    out = T.convert(["2019-06-10 12:00:00", "2019-01-10 12:00:00"], "local", TZ)
    assert list(out["time_status"]) == ["OK", "OK"]
    assert out["timestamp_utc"][0] == utc("2019-06-10 19:00")   # PDT = UTC-7
    assert out["timestamp_utc"][1] == utc("2019-01-10 20:00")   # PST = UTC-8


def test_march_transition_nonexistent_hour():
    out = T.convert(["2019-03-10 01:45:00", "2019-03-10 02:15:00", "2019-03-10 03:00:00"], "local", TZ)
    assert list(out["time_status"]) == ["OK", "NONEXISTENT", "OK"]
    assert out["timestamp_utc"][0] == utc("2019-03-10 09:45")
    assert pd.isna(out["timestamp_utc"][1])
    assert out["timestamp_utc"][2] == utc("2019-03-10 10:00")    # 01:45 PST and 03:00 PDT are 15 min apart


def test_march_day_has_23_hours_and_92_meter_intervals():
    start = T.local_bound_to_utc("2019-03-10 00:00:00", TZ)
    end = T.local_bound_to_utc("2019-03-11 00:00:00", TZ)
    assert len(T.expected_grid_utc(start, end, 15)) == 92
    assert T.local_day_length_hours(["2019-03-10"], TZ)[0] == 23


def test_november_single_occurrence_is_flagged_not_guessed():
    out = T.convert(["2019-11-03 00:45:00", "2019-11-03 01:15:00", "2019-11-03 02:00:00"], "local", TZ)
    assert list(out["time_status"]) == ["OK", "AMBIGUOUS", "OK"]
    assert pd.isna(out["timestamp_utc"][1])
    assert "2019-11-03T08:15Z (daylight)" in out["time_note"][1]
    assert "2019-11-03T09:15Z (standard)" in out["time_note"][1]


def test_november_identical_pair_without_offset_evidence_stays_unresolved():
    # Regression: two identical local timestamps are not evidence of daylight vs standard
    # time (they could be duplicated readings), so neither may be resolved by file order.
    out = T.convert(["2019-11-03 01:15:00", "2019-11-03 01:15:00"], "local", TZ)
    assert list(out["time_status"]) == ["AMBIGUOUS", "AMBIGUOUS"]
    assert out["timestamp_utc"].isna().all() and out["timestamp_local"].isna().all()
    for note in out["time_note"]:
        assert "appears 2 times" in note and "not resolved by order" in note
        assert "2019-11-03T08:15Z (daylight)" in note and "2019-11-03T09:15Z (standard)" in note


def test_november_day_has_25_hours_and_100_meter_intervals():
    start = T.local_bound_to_utc("2019-11-03 00:00:00", TZ)
    end = T.local_bound_to_utc("2019-11-04 00:00:00", TZ)
    assert len(T.expected_grid_utc(start, end, 15)) == 100
    assert T.local_day_length_hours(["2019-11-03"], TZ)[0] == 25


def test_utc_convention_and_unparseable_text():
    out = T.convert(["2024-07-01 19:00:00", "2024-07-01T19:00"], "utc", TZ)
    assert out["timestamp_local"][0] == pd.Timestamp("2024-07-01 12:00", tz=TZ)
    assert out["time_status"][1] == "UNPARSEABLE"


def test_half_open_grid_excludes_end():
    g = T.expected_grid_utc(utc("2019-06-10 00:00"), utc("2019-06-10 01:00"), 15)
    assert list(g) == [utc("2019-06-10 00:00"), utc("2019-06-10 00:15"), utc("2019-06-10 00:30"),
                       utc("2019-06-10 00:45")]
