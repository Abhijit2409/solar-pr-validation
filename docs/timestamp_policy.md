# Timestamp policy (Phase 2)

Implemented in [src/timestamps.py](../src/timestamps.py). Configured in [config/data_structure.yaml](../config/data_structure.yaml). Tested in [tests/test_timestamps.py](../tests/test_timestamps.py) and [tests/test_pipeline.py](../tests/test_pipeline.py).

## Rules

1. **Keep the original text.** `timestamp_original` stores the source text exactly. Parsing is strict (`%Y-%m-%d %H:%M:%S`), and anything else becomes `UNPARSEABLE`. None occurred.
2. **Source clock.**
   - `measured_on` columns are the site's local wall clock. The PVDAQ code is `PST8PDT`; we use `America/Los_Angeles`, which has the same DST rules since 2007.
   - `utc_measured_on` (only in the excluded 2025 POA file) is UTC.
3. **Interval label (documented assumption).** Metadata `time_interval = L` says a timestamp labels the **start** of its averaging interval. That gives `interval_start_utc = timestamp_utc` and `interval_end_utc = start + interval_minutes`. Intervals are **half-open: [start, end)**.
4. **UTC internally.** All keys, grids and ordering use UTC. `timestamp_local` is kept for reading and reporting.
5. **The physical timeline is built in UTC.** Expected intervals run from the scope start to the scope end at each sampling segment's step. A UTC grid never contains the nonexistent March hour and always contains both November 01:xx hours.
6. **Spring forward (March).**
   - Local 02:00–02:59 does not exist. Such a row would get status `NONEXISTENT` and state `TIME_AMBIGUOUS`; none occurred.
   - The normal one-hour jump is **not** counted as missing, because no physical interval exists there.
7. **Fall back (November).** Local 01:00–01:59 happens twice.
   - An ambiguous local timestamp is **never resolved from occurrence count or file order**. Two identical timestamps could be a daylight/standard pair, but they could equally be duplicated readings. Without an explicit UTC offset in the source (no v1 file has one), every occurrence is treated the same:
     - state `TIME_AMBIGUOUS`;
     - `timestamp_utc` stays null;
     - both UTC candidates are written in `quality_notes`, along with the number of occurrences when there is more than one;
     - flag `AMBIGUOUS_LOCAL_TIMESTAMP`.
   - All raw rows are kept. In the real v1 files each fall-back timestamp appears once; no repeated local timestamps exist in any file.
   - The physical intervals behind that hour (8 meter / 24 POA) get `coverage_status = DST_AMBIGUOUS`. They count as neither missing nor covered, and each carries an `UNRESOLVED_DST_INTERVAL` flag.
   - These rows fall between 01:00 and 01:59 local time, when the observed values are zero. Whether they affect PR depends on Phase 3's reporting window and how it treats night-time intervals and night-time energy. Phase 3 must state that treatment.
8. **Reporting day.**
   - `local_reporting_date` is the local calendar date of `interval_start_utc`.
   - `local_day_length_h` is the true length of that day: 23, 24 or 25 hours.
   - Expected intervals per day follow it: the meter has 92 / 96 / 100 per day and POA has 276 / 288 / 300.
9. **Conversion log.** Every timestamp that can't be converted deterministically keeps its original text and a `time_status`: `AMBIGUOUS`, `NONEXISTENT` or `UNPARSEABLE`. The structural flags table lists each one.

## Does a timestamp mark the start of its interval?

This is unresolved. The Phase 2 evidence is in `results/phase2/interval_label_check.csv` and checks V16/V16b.

**Method:**
- Measured 5-minute POA on clear days is compared with a modelled clear-sky POA, at lags of −15 to +15 minutes.
- The model is pvlib's Ineichen clear-sky model with isotropic transposition, a 25° tilt and the metadata azimuth of 180°.
- "Clear days" are selected by shape correlation (≥ 0.998) and an energy ratio of 0.85–1.15 against that model.

**Observation (520 clear days, 2018–2023):**
- Under these model assumptions, measured POA lines up best with the model shifted by about **−11 min**.
- The discrepancy is stable across years and seasons.
- Re-running the same comparison with an assumed azimuth of 190° gives about 0 min in May–Aug and −2 min in Nov–Feb.

**Interpretation:** this is an observed discrepancy under the tested model assumptions. It is not a measurement of the timestamp convention, the clock or the true orientation. Possible explanations, alone or combined:
- the real orientation of the array or POA sensor;
- the timestamp convention (start vs end of interval);
- a clock offset;
- limits of the model: transposition model, clear-day selection, sensor response, horizon shading.

The analysis does not prove a clock fault, does not establish the true azimuth, and does not rule out other explanations.

**Decision:**
- Start labels stay the documented assumption.
- Timestamps and the metadata azimuth (180°) are **unchanged**.
- See the decision log (D12), issue I03 and `results/phase2/azimuth_sensitivity_check.csv`.

## Aligning POA with the meter (for Phase 3; not done in Phase 2)

- Meter intervals start at :00/:15/:30/:45 and last 15 minutes. POA intervals start every 5 minutes and last 5 minutes.
- With start labels, one meter interval [t, t+15) contains exactly the three POA intervals starting at t, t+5 and t+10. Check V18 confirms this holds for every in-scope meter interval.
- Phase 3 can pair each meter interval with those three POA intervals. It must state what happens when fewer than three are usable: `results/phase2/poa_15min_group_completeness.csv` counts the 3/2/1/0 cases.
- Phase 2 does not average, fill or interpolate anything.
