# Data dictionary (Phase 2, config `phase2-v1`)

All tables are Parquet files in `data/processed/` (git-ignored), rebuilt by `src/ingest.py`. Counts are from the run recorded in `results/phase2/run_manifest.json`.

## A. `site_metadata` (1 row)

| Column | Meaning |
|---|---|
| site_id, site_name | 2107, "Farm Solar Array" |
| latitude, longitude, elevation_m | 38.996306, −122.134111, 10.0 (from metadata JSON) |
| local_timezone / source_timezone_code | America/Los_Angeles / PST8PDT |
| tilt_deg, azimuth_deg, tracking, mounting_type | 25.0, 180.0, `f` (fixed), fixed ground mount (Schletter) |
| dc_capacity_raw_value, dc_capacity_raw_unit, dc_capacity_kw, dc_capacity_conversion_rule | 893.0, kW, 893.0, `kW = kW * 1` |
| module_count, module_model, dc_from_module_inventory_kw | 2,880; Hyundai HiS-M310TI; 892.8 (310 W taken from the model name) |
| inverter_count | 24 |
| source, metadata_file, metadata_sha256, metadata_version | DOI, path, hash, S3 Last-Modified + ETag |

## B. `observations` (1,149,798 rows, long format)

There is one row per source row in all six configured files (1,049,291), plus one generated row per missing expected interval (100,507). AC power (15 min) and POA (5 min) stay at their native cadence and are never combined.

| Column | Meaning |
|---|---|
| observation_id | `<source_id>:<source_row>`, or `<sensor_id>:missing:<UTC start>` for generated rows |
| site_id, sensor_id, signal | 2107; `meter_149578` / `poa_149574`; `ac_power` / `poa_irradiance` |
| source_id | `meter_main`, `meter_2024`, `meter_2025`, `poa_main`, `poa_2024`, `poa_2025`; null for generated rows |
| timestamp_original | exact source text |
| timezone_original | local clock (America/Los_Angeles, source code PST8PDT) or UTC |
| timestamp_local, timestamp_utc | converted timestamps; null when not deterministic (see `time_status`) |
| interval_start_utc, interval_end_utc | half-open interval [start, end), assuming interval-start labels |
| interval_minutes, sampling_segment_id | nominal step of the source's sampling segment |
| raw_value | source value as **text**, unchanged |
| raw_unit, standardized_value, standardized_unit, conversion_rule | e.g. `kW = kW * 1 + 0`, `W/m^2 = W/m^2 * 1 + 0` |
| source_file, source_row, source_hash | relative path; file line number (header = line 1); SHA-256 of the file |
| observation_state | controlled vocabulary below |
| value_state | value-only classification (`OBSERVED_VALID`, `OBSERVED_ZERO`, `SOURCE_NULL`, `INVALID_PHYSICAL`), kept even for excluded or duplicate rows |
| exclusion_reason | why a row is out of v1 scope |
| precedence_role | `unique`, `resolved`, `superseded_identical`, `superseded_conflict`, `conflict_unresolved`, `generated` |
| time_status | `OK`, `AMBIGUOUS`, `NONEXISTENT`, `UNPARSEABLE` (blank for generated rows) |
| quality_notes | structural flag types on this row, plus a timestamp note (e.g. the UTC candidates) |

### Observation states

Each row has exactly one state. The order of precedence is `EXCLUDED_BY_SCOPE` > `TIME_AMBIGUOUS` > `DUPLICATE_*` > value state.

| State | Meaning | Count |
|---|---|---|
| OBSERVED_VALID | numeric, non-zero, within physical limits | 406,808 |
| OBSERVED_ZERO | numeric exactly 0; not missing, not judged valid or invalid | 334,240 |
| SOURCE_NULL | source row present but value blank or non-numeric | 0 |
| EXPECTED_ROW_MISSING | generated: expected physical interval with no source row | 100,507 |
| DUPLICATE_IDENTICAL | in-scope row superseded by an identical row | 0 |
| DUPLICATE_CONFLICT | in-scope row that conflicts with another row for the same interval | 0 |
| INVALID_PHYSICAL | outside the plausibility bounds in config `physical_limits` | 1 |
| TIME_AMBIGUOUS | local time ambiguous, nonexistent or unparseable; no UTC assigned | 86 |
| EXCLUDED_BY_SCOPE | outside the v1 window, or from the 2024–2025 increments | 308,156 |

`INVALID_PHYSICAL` uses conservative project screening bounds (config `physical_limits`). These are structural, not experiment thresholds:
- AC power: [−44.65, 893.0] kW. The upper bound is set at the DC nameplate as a screening choice; DC nameplate is not, by itself, a universal physical ceiling on AC output. Observed AC peaks at this site are about 707 kW (inverter clipping).
- POA: [−20, 2000] W/m².

### Unresolved observations requiring review

This category is not a separate state. It is defined as the in-scope rows and intervals below. Together they cover **3,101 distinct in-scope observation rows**, plus 172 expected intervals.

| Member | Level | In-scope count |
|---|---|---|
| `DAYLIGHT_ZERO_UNRESOLVED` flag | row | 3,003 rows (2,988 meter, 15 POA) |
| `SUSPECTED_LOGGER_ARTEFACT` flag | row | 14 rows (11 POA at 1400 W/m², 1 catch-up spike, 2 catch-up zeros) |
| `TIME_AMBIGUOUS` state | row | 86 rows |
| `UNRESOLVED_DST_INTERVAL` flag | expected interval | 172 intervals |
| `UNRESOLVED_CONFLICT_INTERVAL` flag | expected interval | 0 intervals |

The 3,101 rows are the union of the three row-level members. Two catch-up zeros carry both flags, so the parts sum to 3,103.

None of these rows is deleted or repaired. Later phases decide how each affects reporting status.

## C. `expected_intervals` (841,728 rows)

The physical UTC timeline for each in-scope sensor: 2017-11-01 00:00 to 2023-11-02 00:00 local time, at each sampling segment's step.

| Column | Meaning |
|---|---|
| site_id, sensor_id, signal, sampling_segment_id | identifiers |
| interval_start_utc, interval_end_utc, interval_minutes | physical interval [start, end) |
| local_reporting_date, local_day_length_h | local date of the interval start; true day length (23/24/25 h) |
| coverage_status | `COVERED`, `MISSING`, `DST_AMBIGUOUS` (possibly covered by an ambiguous fall-back row), `CONFLICT_UNRESOLVED` |
| resolved_observation_id, resolved_observation_state | the row that covers this interval |
| n_source_rows | in-scope source rows keyed to this interval |

| Sensor | Covered | Missing | DST-ambiguous | Total |
|---|---|---|---|---|
| meter (15 min) | 210,092 | 292 | 48 | 210,432 |
| POA (5 min) | 530,957 | 100,215 | 124 | 631,296 |

## D. `structural_quality_flags` (235,999 rows)

These are structural flags only; no Phase 5 detector decisions. Each row has `flag_id`, `site_id`, `sensor_id`, `flag_type`, `observation_id`, `interval_start_utc`, `timestamp_original`, `source_file`, `source_row`, `evidence`, `rule_id` and `config_version`.

| Flag type | Rule | Meter | POA |
|---|---|---|---|
| MISSING_EXPECTED_INTERVAL | E-GRID | 292 | 100,215 |
| IRREGULAR_INTERVAL (gap from previous resolved row ≠ nominal step) | E-STEP | 9 | 36,910 |
| AMBIGUOUS_LOCAL_TIMESTAMP (all files, incl. excluded) | T-AMBIG | 40 | 62 |
| UNRESOLVED_DST_INTERVAL | T-AMBIG | 48 | 124 |
| INVALID_VALUE | R-LIMITS | 1 | 0 |
| SUSPECTED_LOGGER_ARTEFACT: catch-up spike plus its 2 preceding zeros | A-CATCHUP | 3 | – |
| SUSPECTED_LOGGER_ARTEFACT: exact 1400 W/m² ceiling (all files) | A-CEILING | – | 13 |
| DAYLIGHT_ZERO_UNRESOLVED | A-DAYZERO | 2,988 | 15 |
| OVERLAP_IDENTICAL_ACROSS_FILES (2024 vs 2025 increments) | P-RANK | 29,175 | 66,103 |
| SAMPLING_REGIME_CHANGE (2025 POA file: 5 → 15 min from Nov 2024) | S-SEG | 0 | 1 |
| SOURCE_NULL, DUPLICATE_TIMESTAMP_*, CONFLICTING_OVERLAPPING_SOURCE, NONEXISTENT/UNPARSEABLE_TIMESTAMP, OFF_GRID_TIMESTAMP, UNRESOLVED_CONFLICT_INTERVAL | – | 0 | 0 |

`DAYLIGHT_ZERO_UNRESOLVED` is applied to an in-scope observed zero when either condition holds:
- the solar elevation at the interval midpoint is above 10°;
- for AC power only, any resolved POA reading inside the same 15-minute interval is above 200 W/m². This is a lookup, not an average.

It means "cause unknown". It does **not** mean missing, a confirmed outage or valid operation.

## E. `sampling_segments` (7 rows)

Contains the modal step per calendar month for each source; consecutive months with the same step form one segment. Each of the two primary sources has a single segment (meter 15 min, POA 5 min). The only regime change is in the excluded 2025 POA file (5 → 15 min from 2024-11).

## Results files (`results/phase2/`)

| File | Content |
|---|---|
| structural_validation_summary.csv | checks V01–V18, with `required` and result (PASS / FAIL / INFO / SKIPPED) |
| validation_manifest.json | overall status, exit code, result counts, validator/code/config hashes, timing, package versions |
| run_manifest.json | pipeline config, code and input hashes; row counts; table content hashes |
| dst_policy_change_comparison.json | before/after table hashes for the external-review DST change |
| azimuth_sensitivity_check.csv | clear-day lag at assumed azimuths 180°, 190° and 200° |
| observation_state_counts.csv, flag_counts.csv | count tables |
| row_reconciliation.csv | reconciliation of source rows, generated rows, states and coverage |
| dst_transition_summary.csv | per DST date and sensor: day length, expected intervals, coverage, ambiguous rows |
| poa_15min_group_completeness.csv | number of usable 5-min POA intervals in each 15-min meter interval, by year and daylight |
| interval_label_check.csv | per day: best lag of measured POA against a clear-sky model |
