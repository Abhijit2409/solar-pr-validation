# Decision log

## Phase 1: site search and audit (2026-10-26)

### D1. Project location
- New folder `D:\solar-pr-validation` with its own Git repository (initialised, nothing committed).
- `D:\Downloads\wind-ai-demo` and `D:\Clir_Case_Study` were listed only, not modified.

### D2. Data source and access
- PVDAQ on OEDI ([DOI 10.25984/1846021](https://doi.org/10.25984/1846021)), licence CC BY 4.0.
- No AWS CLI is installed, so files were fetched anonymously over HTTPS from the public bucket with `src/fetch_oedi.py`. That script records the URL, access time, size, SHA-256, ETag and Last-Modified for each file.

### D3. Candidates considered

| System | How deeply inspected | Outcome | Reason |
|---|---|---|---|
| **2107** Farm Solar Array, Arbuckle CA (893 kW, fixed) | Metadata JSON, change logs, meter + POA files (all years), inverter files 2017–2023 (v1) and 2024 | **Selected** | Plant revenue meter in kW; documented POA channel in W/m²; DC capacity reproducible from module count; boundary verified against inverter sum in every year; ~5.7 years of usable paired data; no QA issue in index |
| 1433 NREL RSF1 (449 kW, roof) | Metadata JSON | Rejected | No irradiance instrument listed; module list gives 2 × 936 × 205 W = 383.8 kW, conflicting with 449.28 kW |
| 1430 NREL Mesa (721 kW, single-axis tracker) | Metadata JSON | Rejected | Tracker (POA plane moves); no irradiance instrument listed; tilt/azimuth blank |
| 4 NREL x-Si-1 (1 kW) | Metadata JSON | Rejected | 1 kW test system; no instruments listed; index flags clipping > 10% and inconclusive orientation |
| 2105 Maui Ocean Center | Index row only | Not pursued | Tilt, azimuth and mounting blank; index flags < 10% daytime values |
| 9068 / 9069 / 7333 | Index row only | Not pursued | 7.5 GB / 40 GB / 930 GB; QA flags (9068, 9069); 7333 index qa_status fail |
| 1332 NREL Parking Garage | Index row only | Not pursued | Two conflicting tilt rows (16.77° and 60°) for the same system |

Data files were downloaded only for 2107. The other rejections rest on metadata or index evidence, as stated in the table.

### D4. Site-suitability decision: **SUITABLE_WITH_LIMITATIONS**

PR inputs that are verified compatible:
- AC average power (kW) from the plant revenue meter. It covers all 24 inverters: the meter/inverter-sum median is 0.976–0.985 in each year 2018–2023 and 0.982 in 2024.
- Relative timing: POA vs inverters lines up at zero shift in two winters inside 2018–2023 and in 2024. Meter vs inverters is lag-tested in 2024 only.
- POA irradiance (W/m²).
- DC capacity 893.0 kW, reproduced from the module inventory, for the same system boundary.
- Coordinates and local timezone with DST, verified from data behaviour.

Limitations that remain:
- I03: interval alignment comes from metadata only, and absolute timing has up to ~20 min ambiguity against calculated solar noon.
- I17: POA is nearly empty from Nov 2017 to Jan 2018.
- I18: meter and inverter data agree less closely in 2018 and 2021.
- I12: the POA sensor is undocumented and has no redundant sensor.
- I04: pre-existing POA gaps.
- I06/I07: unexplained daylight zeros.

None of these blocks a defensible PR calculation. They limit what Phase 5–7 may claim.

### D5. Primary candidate window
- Use the main files covering 2017-11-01 to 2023-11-01, where both channels exist.
- Usable paired coverage effectively begins in **2018-02**. Nov 2017–Jan 2018 has 69–90% of daytime POA missing (I17), and Mar–Apr 2018 is partly covered.
- Exclude for v1:
  - pre-2017-11 meter-only data (I09);
  - the 2024–2025 increments, because of the mixed timestamp conventions (I01) and the overlap (I10).
- The increments stay available. They may later serve as an extra holdout only if each file's convention is handled separately.
- The development/evaluation split is not chosen here. It is a Phase 4 decision.

### D6. Inspection scope disclosure (for the Phase 4 freeze)
Phase 1 profiled values across **all** downloaded years (2017–2025):
- percentiles and yearly maxima;
- monthly timing midpoints;
- daytime missingness by hour;
- daylight-zero events;
- overlap correlations between files.

Profiling used these working heuristics. They are **not** candidate rule values:
- POA > 200 W/m² for "sunny";
- POA > 20 W/m² for "daylight" in lag tests;
- 08:00–17:00 local clock for "daytime";
- 720 kW as the catch-up ceiling;
- runs of at least 2 h for flatlines.

No thresholds, PR values or status rules were set. No windows were labelled development or evaluation. Phase 4 must treat this whole-record profiling as already seen when it justifies its split.

Flatline profiling covered only non-zero runs of identical consecutive records lasting at least 2 h. Such a run can span a dropout. Shorter POA flatlines were not profiled, so "no existing POA flatlines" means none of at least 2 h.

### D7. Raw data handling
- Raw files sit unchanged under `data/raw/` in their original S3 paths and names.
- `src/build_manifest.py` re-hashed all 22 files: none changed since download.
- `data/raw/pvdaq/` is git-ignored because of its size (~520 MB, mostly the 2017–2023 inverter file used only for the boundary check). It can be reproduced from `src/fetch_oedi.py` plus the hashes in `docs/source_manifest.json`. `data/raw/download_log.jsonl` stays tracked.

### D8. Environment
- Python 3.11.0 (the `python` command) was the interpreter used. The `py` launcher resolves to 3.13.5 and was not used.
- pandas 3.0.5, numpy 2.4.6 and requests 2.34.2 are the only packages exercised in Phase 1.
- pvlib and pvanalytics are not installed. Install them only when a phase needs them.

### Assumptions still open
1. Timestamps mark interval start ('L' in metadata). The relative alignment of the channels is consistent, but the absolute convention is unverified.
2. The POA sensor sits in the array plane (25°, 180°). This is inferred from the channel name only.
3. Which of the two fall-back 01:xx hours was kept, or whether they were merged, is unknown. It is night-time only.
4. The 1400 W/m² values are a sensor/logger ceiling.
5. Daylight meter zeros (42 non-catch-up runs) have an unknown cause. They are not assumed to be real zero production or a data fault.
6. The 0.64 kW meter steps reflect pulse-based energy measurement. This is inference only.

## Phase 2: raw-to-standardized data layer (2026-10-27)

### D9. Environment
- Project virtual environment `.venv` (git-ignored) on Python 3.11.0, with these versions (pinned in `requirements.txt`):
  - pandas 3.0.5, numpy 2.4.6, pyarrow 25.0.1, requests 2.34.2;
  - pvlib 0.16.1 (solar geometry only), PyYAML 6.0.3, pytest 9.1.1.
- pvlib function signatures were checked before use.

### D10. Data model and outputs
- Five Parquet tables in `data/processed/`: `site_metadata`, `observations`, `expected_intervals`, `structural_quality_flags` and `sampling_segments`. They are git-ignored, about 71 MB, and rebuilt by `src/ingest.py`.
- Schemas and counts: `docs/data_dictionary.md`.
- Config: `config/data_structure.yaml` (version `phase2-v1`). Its structural bounds and flag heuristics are **not** experiment thresholds.

### D11. Scope and source precedence
- All six configured files (meter and POA: main, 2024, 2025) are ingested so that every source row keeps its lineage.
- Only the main files inside [2017-11-01, 2023-11-02) local time are in scope. Everything else is `EXCLUDED_BY_SCOPE` with a reason.
- Precedence: main > 2024 > 2025 (`docs/source_precedence.md`).
- Measured outcome: no duplicates inside any file, and no conflicts. The 2024 and 2025 increments overlap on 95,278 rows, all identical within 1e-6.
- So the real data exercised only the identical cross-file path. The same-file duplicate, same-rank conflict and cross-file conflict paths are verified by synthetic tests only (`tests/test_pipeline.py`).
- Weather and inverter files are not part of the observations table.

### D12. Timestamps and interval labels
Policy: `docs/timestamp_policy.md`.
- **Ambiguous fall-back times are never resolved from occurrence count or file order** (revised after external review; the earlier code resolved exact pairs by file order).
  - In the real files each fall-back timestamp appears once, so this revision changes no real-data output. It only affects an untested edge case, which now has a regression test.
  - Totals: 86 `TIME_AMBIGUOUS` rows in scope and 172 `DST_AMBIGUOUS` expected intervals.
- **Clear-day POA timing, an observed discrepancy:**
  - Measured POA was compared with a modelled clear-sky POA: pvlib Ineichen, isotropic transposition, 25° tilt, metadata azimuth 180°.
  - On 520 clear days in 2018–2023, the best alignment is a lag of about **−11 min**, stable across years and seasons.
  - With an assumed azimuth of 190°, the same comparison gives about 0 (May–Aug) and −2 min (Nov–Feb). At 200° it overshoots (`results/phase2/azimuth_sensitivity_check.csv`).
  - Possible explanations, alone or combined: the real orientation of the array or POA sensor, the timestamp convention, a clock offset, or model limitations (transposition model, clear-day selection, sensor response, horizon).
  - The analysis does not prove a clock fault, does not establish the true azimuth, and does not rule out other explanations.
- **Decision:** keep interval-start labels as the documented assumption. Timestamps and the metadata azimuth stay unchanged.
- **Consequences:**
  - Phase 5/6 timing checks must compare against the site's own baseline lag, measured on development data under a stated model, not against zero.
  - Any geometry-based daylight rule, including the 10° elevation criterion in A-DAYZERO, has uncertainty of roughly this size at the edges of the day.
  - Whether the night-time DST-ambiguous rows affect PR depends on Phase 3's reporting window and its treatment of night-time intervals and energy.

### D13. State and flag rules
- **`INVALID_PHYSICAL`:** AC power outside the conservative **project screening bounds** of [−44.65, 893.0] kW, or POA outside [−20, 2000] W/m².
  - The 893 kW AC upper bound is set at the DC nameplate as a screening choice. DC nameplate is not, by itself, a universal physical ceiling on AC output.
  - At this site, observed AC peaks are about 707 kW because the inverters clip at about 24 × 30.1 kW, so the bound sits well above normal operation.
  - Only one row is affected: the 1,783.68 kW catch-up spike. It keeps its raw value and source row, and the spike plus its two preceding daylight zeros are flagged `SUSPECTED_LOGGER_ARTEFACT` (A-CATCHUP).
- **POA readings of exactly 1400 W/m²:** kept as `OBSERVED_VALID` and flagged (A-CEILING).
- **`DAYLIGHT_ZERO_UNRESOLVED`:** solar elevation above 10°, or, for the meter, any POA reading above 200 W/m² inside the same meter interval.
  - **Development iteration, disclosed:** the first run used geometry only. That flagged 221 of Phase 1's 341 sunny-zero intervals, missing low-sun intervals with bright POA, so the POA lookup was added.
  - Final result: 2,988 meter intervals flagged, and all 41 in-scope Phase 1 runs are covered.
  - **Scope-driven deviation:** Phase 1 counted 43 sunny-zero runs. The 41 in v1 scope are `OBSERVED_ZERO` with the flag. The other 2 are in the excluded 2024 increment: they are `EXCLUDED_BY_SCOPE` with `value_state = OBSERVED_ZERO` and carry no daylight-zero flag, because that flag is computed only in scope.

### D14. Inspection disclosure
- Phase 2 again read values across 2017–2023: daylight-zero distribution, clear-day timing, and POA group completeness.
- The 2018–2023 data has been profiled twice now (Phase 1 and Phase 2). A later development/evaluation split inside this period must **not** be described as unseen raw data. Phase 4 can still freeze hidden synthetic fault cases before evaluation.
- No PR, energy, irradiation, detection thresholds or status rules were computed or chosen.

### D15. Validation (final run after external review, 2026-10-27 01:25–01:35 UTC)
- `src/validate_structure.py` now exits 1 if any required check fails, 2 if a required check is skipped (INCOMPLETE), and 0 only when every required check passes. INFO checks never affect the exit code.
- **Final full run:** overall `PASS`, exit code 0. 18 required checks PASS, 0 FAIL, 0 SKIPPED, 5 INFO (V14b, V15, V16, V16b, V17). Runtime 566.7 s.
- Hashes, versions and timing are in `results/phase2/validation_manifest.json`. Recorded code and config hashes were re-checked against the files on disk and match.
- Raw hashes are unchanged (22/22).
- The re-run (V09) reproduced identical table contents and identical Parquet bytes.
- `pytest`: 22 tests passed. These include the DST identical-pair regression test and four failure-propagation tests that don't use the real dataset.
- **Earlier runs, disclosed:**
  - One validation run was stopped before it finished.
  - One crashed in V14, because `results/phase1/daylight_zero_events.csv` had been re-saved outside the pipeline at 2026-10-26 18:08 local. The dates became DD-MM-YYYY HH:MM and seconds were dropped. Its 43 events are identical to a regeneration from code.
  - That file was **left as found**. V14 now recomputes the Phase 1 events from raw data, and V14b (INFO) reports whether the saved file still matches.

### D16. External review changes (2026-10-27)
- **DST policy:** pairs are no longer resolved by file order (see D12). The before/after comparison is in `results/phase2/dst_policy_change_comparison.json`: observations, expected intervals, site metadata and segments are identical, and flags differ only in one reworded evidence string.
- **Wording:** the ~11 min POA/model offset is now described as an observed discrepancy under tested model assumptions. The AC upper bound is described as a conservative project screening bound.
- **Night-time PR effect:** now stated conditionally on Phase 3's reporting window.

## Phase 3: PR definition, implementation and arithmetic validation (2026-10-27)

### D17. Inputs verified before use
- Phase 2 validation status `PASS` (exit 0). Raw files 22/22 unchanged. Code and config hashes match `validation_manifest.json`.
- **Processed tables are pinned by file-byte SHA-256** (`results/phase3/phase3_manifest.json`) rather than by the Phase 2 in-memory content hash.
  - Reason: the `structural_quality_flags` in-memory hash doesn't reproduce after a Parquet read. Three columns are `object` in memory, and `source_row` becomes `float64` on read-back.
  - A fresh rebuild produced byte-identical Parquet files for all five tables, so the content is unchanged.
- What this establishes:
  - **link to Phase 2:** the byte-identical rebuild above, and 4 of 5 tables matching their Phase 2 content hashes;
  - **Phase 3 check P02:** the tables have not changed since the Phase 3 run (file SHA-256), nothing more.
- The reformatted Phase 1 events CSV is left as found.

### D18. Metric and window (`docs/metric_definition.md`, `config/metric_definition.yaml`, `phase3-v1`)
- **Metric:** non-temperature-corrected **daylight-window PR**, stored as a ratio.
  - Capacity: 893.0 kW (metadata) everywhere; the 892.8 kW inventory value is not used.
  - G_STC: 1 kW/m².
- **Window:** solar geometry (pvlib), sun elevation above 5° at the meter interval midpoint (provisional). Night, including DST-ambiguous intervals, is always excluded.
- **Pairing:** a meter interval [t, t+15) pairs only with its three POA intervals at t, t+5 and t+10. Boundaries are checked to the nanosecond. Any unusable POA excludes the whole interval from both E_AC and H_POA. Nothing is interpolated or substituted.
- **Eligibility, all provisional:** written before any real PR value was computed, and **not changed after seeing results**.
  - time coverage ≥ 0.90;
  - clear-sky-weighted coverage ≥ 0.90;
  - H_POA ≥ 0.5 kWh/m².
- **Flag policy:**
  - withhold → `REVIEW_REQUIRED`: daylight zeros, catch-up, invalid input, unresolved time;
  - context only: the 1400 W/m² ceiling, irregular intervals, missing intervals (already in coverage).
- **Only zeros carrying the Phase 2 flag withhold a day.** The flag starts at 10° (or POA > 200 W/m²) while the window starts at 5°. Paired zeros in the 5–10° band with lower POA therefore withhold nothing. Both thresholds were declared before results, so neither is changed in Phase 3; Phase 4 aligns them (issue I23).

### D19. Development period
- Local dates 2018-02-01 to 2020-01-31 (730 days). All Phase 3 real-data results and examples come only from these dates.
- 2020-02-01 to 2023-11-01 was not computed or used for metric choices. That period was profiled in Phases 1–2, so it is **not** unseen raw data.

### D20. Measured development outcomes (calculation/input eligibility only, not detector results)
- **Reconciliation:**
  - 70,080 expected meter intervals; 32,556 in the window;
  - 31,709 paired (7,927.25 h);
  - 847 excluded: 846 `POA_MISSING` (211.5 h) and 1 `METER_INVALID` (the 1,783.68 kW spike);
  - 0 DST-ambiguous intervals in the window;
  - 95,127 POA observations used, 3 per pair, none reused.
- **Days:**
  - 515 `CHECKS_PASSED`: reported PR median 0.793, IQR 0.752–0.828, range 0.552–0.952;
  - 180 `REVIEW_REQUIRED`;
  - 35 `INSUFFICIENT_DATA`.
- **Review burden (24.7% of days):** almost entirely `UNRESOLVED_ZERO_OUTPUT`.
  - 886 window meter intervals on 189 days read exactly 0, mostly at 06–07 h and 18–19 h local.
  - POA during these zeros is mostly 25–200 W/m². Comparable non-zero intervals produce a median 27 kW at 25–50 W/m² and 52 kW at 50–100 W/m², so these zeros are not ordinary low-light output.
  - On most review days they carry 1–5% of the day's paired irradiation.
  - The withholding rule was kept as declared. The burden is reported, not tuned away; Phase 4 decides the final policy.
- **Unflagged zeros:** 542 paired window zeros on 226 days carry no daylight-zero flag. 50 of those days are `CHECKS_PASSED`; there the zeros carry a median 0.3% of paired H, and removing them would change PR by at most 1.29 percentage points (diagnostic comparison only, `results/phase3/unflagged_zero_days_dev.csv`).
- **Daily PR spread on passed days:** median PR falls from 0.87 (H ≤ 1 kWh/m²) to 0.785 (H 5–9 kWh/m²). 33 passed days with H ≥ 3 kWh/m² read above 0.85, mostly in cooler months.
  - Possible contributors include the missing temperature correction, inverter clipping (AC capped near 707 kW on an 893 kW array), irradiance-dependent effects, and POA sensor under-reading or calibration.
  - None of these was tested, so a PR value alone is not treated as evidence of either a fault or normal operation.

### D21. Validation
- `src/validate_phase3.py`: 13/13 checks PASS, exit 0 (`results/phase3/phase3_validation_summary.csv`, `phase3_validation_manifest.json`). It covers:
  - raw and processed inputs unchanged;
  - reconciliation;
  - no contribution from unpaired intervals;
  - reported PR withheld unless passed;
  - no infinite or substituted PR;
  - reproducible re-run;
  - the independent check;
  - pytest.
- **pytest:** 41 passed (22 from Phase 2 + 19 new Phase 3 known-answer and edge-case tests). The hand-worked example (100 kW, 4 × 40 kW, 12 × 500 W/m²) gives E = 40 kWh, H = 0.5 kWh/m², PR = 0.8 (80%).
- **Independent check:** `src/independent_check_phase3.py` recomputes 8 development days straight from the raw CSVs. All 8 match production exactly on window and paired interval counts, energy, irradiation and PR.
  - **Independently verified:** parsing, local→UTC conversion, pairing and arithmetic, because the check uses its own code for these.
  - **Not independently verified:** the window geometry. Both use pvlib's solar position.

## Phase 4: experiment design and specification freeze A (2026-10-27)

### D22. Evidence verified before design
- Phase 3 validation is `PASS`. Its code and config hashes, the paired-interval output, the independent check (8/8), raw files (22/22) and processed tables (5/5) all match. pytest: 41 passed.
- `daily_pr_dev.csv` differs from its manifest only in file bytes (CRLF line endings). The in-memory hash matches, and a recomputation is line-identical (731/731 lines).
- Phase 4 pins Phase 3 outputs by file-byte SHA-256 in `results/phase4/specification_manifest.json`.

### D23. Periods
- Development: [2018-02-01, 2020-02-01). Evaluation: [2020-02-01, 2023-11-01).
- The evaluation period is not unseen raw data:
  - Phases 1–2 profiled it structurally, and the Phase 2 clear-day timing analysis included it.
  - Phase 4 inspected only its structural eligibility.
  - No evaluation PR or detector output has been computed.

### D24. Shared rules vs methods (`config/experiment.yaml` `experiment-v1`; `docs/experiment_protocol.md`)
- **Shared:** the Phase 3 arithmetic (capacity, formula, window, pairing, invalid and unavailable handling, H ≥ 0.5 kWh/m², precedence).
- **Basic:** time coverage ≥ 0.90, range, time/duplicates, sampling regularity, catch-up flag and the zero-output policy.
- **Improved:** basic plus midday (clear-sky-weighted) completeness ≥ 0.90, POA stale (≥ 6 identical values within 0.05 W/m²) and clear-sky timing consistency (baseline −11, threshold 8 min).
- Clear-sky-weighted coverage moved from shared eligibility (Phase 3) to **improved-only**.
- `config/metric_definition.yaml` (`phase3-v1`) is unchanged, and the Phase 3 results are preserved as the original run.

### D25. Zero-output policy (resolves I21, I23)
- Covers the whole window from 5°:
  - every zero stays a recorded observation in E_AC;
  - **review** if a meter zero coincides with POA ≥ 200 W/m², if zeros carry ≥ 1% of the day's paired H, or if POA reads 0 under clear-sky POA ≥ 200 W/m²;
  - a confirmed fault is never asserted.
- **Chosen after inspecting development data.** The zero rate by POA (24–26% below 50 W/m², 0.6–1.5% above 200 W/m²) motivated the 200 W/m² threshold.
- **Materiality:** 0.5% / 1% / 2% give 171 / 123 / 34 review days. 1% was chosen as the stricter plausible cap on PR bias, not for its pass rate.
- **Development effect** (730 days):

  | Policy | Passed | Review | Insufficient |
  |---|---|---|---|
  | phase3-v1 | 515 | 180 | 35 |
  | basic | 574 | 123 | 33 |
  | improved | 572 | 123 | 35 |

- On basic-passed days the remaining zeros carry ≤ 0.98% of H. Their PR effect is a median −0.29 pp and at most 0.68 pp.

### D26. Timing check design
- **Rejected reference — meter vs POA:** day-level best lag ranged −25 to 0 min, and a ±10 min threshold would flag 304 of 664 development days (46%).
- **Chosen reference — the clear-sky geometry model:**
  - conclusive on 155 of 721 development days (corr ≥ 0.998 and energy ratio 0.85–1.15);
  - baseline −11 min (IQR −12 to −10.5);
  - an 8 min threshold flags 0 development days.
- **Limitations:** inconclusive on 79% of development days, and almost always in winter. The check is retrospective and same-day. It does not correct the −11 min discrepancy or claim a cause.

### D27. Faults, windows, scoring, freeze
- **Primary faults:**
  - POA rows removed for 30/60/120/240 min;
  - POA-only timestamp shift of ±15/30/60 min;
  - POA hold-last flatline for 15/30/60/120/240 min.
- **Secondary faults:** explicit nulls, whole-file shift, a fixed 600 W/m² flatline.
- **Window eligibility** is structural only: 1,270 of 1,369 evaluation days.
- **Frozen sample:** 96 disjoint windows (8 per family per season, seed 20261027) in `results/phase4/evaluation_window_sample.csv`.
- **Scoring:** separates `ALREADY_WITHHELD`, `DETECTED_SPECIFIC`, `WITHHELD_OTHER`, `INCONCLUSIVE`, `MISSED` and `UNSCORABLE`, and reports denominators, unique windows, pp and relative PR change, and unconfirmed alerts on unmodified data.
- **Specification freeze A:** recorded in `results/phase4/specification_manifest.json` (`src/validate_spec_phase4.py`: 10/10 PASS) and a local Git commit.
- **Implementation freeze B** follows Phases 5–6.

## Phase 5: detector implementation and development reproduction (2026-10-27)

### D28. Starting point verified
- All 23 `experiment-v1` specification files match `results/phase4/specification_manifest.json`. Raw (22) and processed (5) data are unchanged, and the working tree equalled commit `2952d87` before work began.

### D29. Amendment experiment-v1 → experiment-v2 (`docs/amendments.md`)
- **A1:** the headline detection rate includes INCONCLUSIVE in the denominator. The conclusive-only rate and timing applicability are secondary.
- **A2:** zero materiality is a PR sensitivity, not an error bound or a fault finding.
- **A3:** timing is INCONCLUSIVE at the ±90 boundary or with undefined correlation. Adds a tie rule, and defines eligible readings as POA inside in-window meter intervals.
- **A4:** catch-up, duplicate and sampling evidence are recomputed from the supplied observations.
- **A5:** B5d scope is pinned down.
- **A6:** stale runs are counted in readings.
- **A7:** a passed day with inconclusive timing shows "timing not verified".
- **Not changed:** any threshold, period, fault, seed, eligibility rule or the window sample (check P5-02: every v1 value is identical except the version and scoring rates).
- `experiment-v1` is preserved unchanged at `2952d87`.

### D30. Implementation (`docs/check_definitions.md`)
- **Detector input:** only `obs_ref, signal, interval_start_utc, value, unit`, plus site metadata, measurement metadata, frozen configs and the development timing baseline (−11).
- **Recomputed from current inputs:** grid, window, states, duplicates, off-grid rows, pairing and all evidence.
- **Reused shared code:** Phase 3 `pair_intervals` and `performance_ratio`.
- **Improved = basic + I1/I2/I3.** An assertion stops the run if improved ever passes a day basic withholds.
- **Tests:** 28 synthetic detector tests, so 69 in total, all passing.

### D31. Development reproduction (development dates only)
- **Statuses:** basic 574/123/33 and improved 572/123/35. These equal the Phase 4 prototype day by day in status, reason codes and context codes for all 730 days of both methods.
- **Shared arithmetic:** equals the Phase 3 daily table, within 1e-12.
- **Timing:**
  - 575 inconclusive (538 not clear, 20 at the boundary, 17 too few readings) and 155 conclusive;
  - conclusiveness agrees with the prototype every day;
  - best lag differs on 39 inconclusive days only.
- **Prototype deviation identified:** its eligible-reading gate used each 5-minute sub-interval's own sun elevation instead of in-window meter membership. That changes 1–3 edge readings on 161 days. On 2019-01-26 the count is equal but one edge reading is swapped.
  - The implementation follows the specification (A3).
  - Code was **not** changed to match the prototype.
  - No status, reason or conclusiveness changed.
- **Limitation shown:** 463 improved-passed days carry the "timing not verified" limitation.
- **Validation:** `src/validate_phase5.py` 11/11 PASS (`results/phase5/validation_manifest.json`).
  - One check failed on the first run, P5-09, a crude text search that matched the word "scenario" in a docstring stating what the detector never receives. It was replaced by a syntax-tree inspection of identifiers and strings.
- **Not implementation freeze B:** the injector and evaluator are Phase 6.

## Phase 6: injector, evaluator, runner and implementation freeze B (2026-10-27)

### D32. Starting point verified
- `experiment-v1`: 23/23 specification files match.
- Phase 5: 19 code, config and test files match.
- Data and sample: raw 22/22, processed 5/5, sample 96 unique windows.
- Tests: 69 passing; clean tree at `f1ff436`.

### D33. Amendment experiment-v2 → experiment-v3 (`docs/amendments.md` A8–A14)
Settles the ambiguities in the frozen fault text before any case existed:
- the detector context is [D−2, D+3);
- the missing-data centre must fit the 240-minute block;
- the shift block and the definition of a collision;
- the flatline start and the hold-last source;
- no-effect cases are unscorable;
- order-independent seeds from `sha256(window_id)`;
- the category precedence;
- alert burden counted in unique days.

No threshold, period, severity, seed value or sample changed (P6-03).

### D34. Implementation
- **`src/inject_faults.py`:** works only on copies. Truth records hold the placement, affected refs (with their observation ids), the number of rows affected, the number of values changed, collisions and status. Refs are reassigned in one opaque format.
- **`src/evaluate.py`:** pure scoring and summaries. The headline rate keeps INCONCLUSIVE in the denominator, null on a zero denominator. Alert burden counts unique days.
- **`src/run_evaluation.py`:** commands `smoke`, `freeze`, `verify-freeze` and `evaluate`.
  - Detector outputs are keyed by an opaque `run_key`; truth is saved separately.
  - Failures are kept, and existing run directories are never overwritten.
- **`src/freeze_b.py`:** hashing and verification against any root.
- **Tests:** 48 new (injector 26, scorer 15, full path 5, freeze 4), so 117 in total, all passing.

### D35. Development smoke run (labelled; not evaluation)
- **Inputs:** days 2019-01-15, 2019-04-15, 2019-07-15 and 2019-10-15, with all three families each. That is 12 windows, 96 detector runs and 84 cases, all `APPLIED`.
- **Disjoint** from the frozen sample's windows and dates.
- **Behaviour matched the specification:**
  - 2019-01-15 was already withheld (`LOW_IRRADIATION`), so its cases are scored `ALREADY_WITHHELD` even where I2 fired;
  - 15-min flatlines were missed (4 readings);
  - shifts were detected on the conclusive day and `INCONCLUSIVE` on non-clear days;
  - collisions equal |s| ÷ 5 for POA.

### D36. Implementation freeze B (`results/phase6/implementation_freeze_manifest.json`)
- **Contents:**
  - 36 files: runtime code (14), configs and spec/amendment docs (9), frozen inputs (6: sample, development timing summary, eligibility, spec manifest, source manifest, requirements) and tests (7);
  - the hashes of the 5 processed tables;
  - package versions (Python 3.11.0, pandas 3.0.5, numpy 2.4.6, pyarrow 25.0.1, pvlib 0.16.1, PyYAML 6.0.3, scipy 1.17.1);
  - base commit `f1ff436`.
- **Self-hashing:** the manifest does not hash itself.
- **Verification:** `verify-freeze` passes.
- **Protection is tested on temporary copies:**
  - a tampered config, a deleted file and version drift are reported;
  - `evaluate` refuses to run on a mismatch and creates no `results/phase7`.
- **Validation:** `src/validate_phase6.py` 8/8 PASS (`results/phase6/validation_manifest.json`).
- **No evaluation case was generated or run;** `results/phase7` does not exist.

## Phase 7: frozen evaluation run1 (2026-10-27)

### D37. Execution
- **Before the run:** freeze B verified, `experiment-v3` active, 96-window sample, and `results/phase7/run1` absent.
- **Command:** `src/run_evaluation.py evaluate --run-id run1`.
- **Result:** process exit code 0, 03:09–03:15 UTC. Log and exit code were captured to files beside the run.
- **Accounting:** 96 windows, 768 detector runs, 672/672 cases applied, 0 unscorable, 0 failed windows, 1,344 scores, 1,270 population days.
- **After the run:** freeze B verified again, and run1 outputs are byte-identical to their hashes recorded at completion.
- **No code, threshold, scoring, placement or exclusion was changed after results were seen.**

### D38. Measured findings (primary scenarios; headline denominators include inconclusive cases)
- **Flatline:**
  - basic 0/28 at every severity;
  - improved 0/28 at 15 min (expected, 4 readings) and **28/28** at 30, 60, 120 and 240 min.
- **Missing daytime:**
  - 30 min: both 0/26;
  - 60 min: basic **8/26**, improved **15/26**;
  - 120 and 240 min: both 26/26.
- **POA-only shift:**
  - basic 0/168 recognised (22 withheld through zero-output or low-irradiation rules);
  - improved 29/168 recognised, **113 inconclusive**, 26 withheld through other rules.
  - Timing applicability: DJF 2%, MAM 13%, JJA 7%, SON 42%.
  - Lag estimates tracked the injected shifts even on inconclusive days; the clear-day gate (corr ≥ 0.998) withheld the verdict. On windows whose counterpart was conclusive, 43 of 50 faulted copies were flagged.
- **Secondary scenarios** mirror the primary ones.
- **The common-subset view** equals the official denominators, because the already-withheld windows were identical for both methods.
- **PR effect:**
  - basic still reported flatlined days with PR changes up to +85.6 pp (33 cases ≥ 5 pp);
  - improved's remaining misses moved PR by ≤ 2.7 pp;
  - no case had unavailable PR.
- **Unconfirmed alert burden on unmodified days:** 177/1,270 (13.9%) for **both** methods (165 review, 12 insufficient). Improved added no burden.

### D39. Audit and validation
- **Scoring audit:** AI-assisted independent re-scoring (`src/audit_phase7.py`, which does not import the scorer). 26/26 items PASS, 0 category and 0 PR mismatches. No human review was performed.
- **Phase 7 validation:** 7/7 PASS (`results/phase7/validation_manifest.json`).
- **Analysis outputs:** in `results/phase7/analysis/`, read-only with respect to run1.
- **Defects:** none found. The timing gate's conservativeness is a design limitation (I26), not an implementation defect.

## Phase 8: presentation package (2026-10-27)

### D40. Evidence verified, then claims fixed before writing
- **Verified first:** run1 outputs 11/11 and analysis files 13/13 matched their Phase 7 hashes, and freeze B verified.
- **Claim register written before any presentation text:** `results/phase8/claim_register.csv` (`src/phase8_claims.py`) recomputes 32 headline claims from saved run1 outputs. Each claim records its source file, filter, numerator, denominator, units and required qualification.
- **Phase 7 report unchanged.** Wording clarifications go in `docs/revision_log.md`:
  - zero MISSED ≠ all shifts detected;
  - the 2.7 pp maximum applies to the MISSED category only;
  - 112/112 = 28 windows × 4 severities;
  - the alert-burden finding is site-specific;
  - the unmodified PR is a comparison reference.

### D41. Deliverables
- **Figure** (`presentation/figure_flatline_example.png` and `.svg`, `src/phase8_figure.py`):
  - Case `flatline_poa-DJF-07|primary:hold_last|+240`, 2023-01-14.
  - Rebuilt from the Phase 2 observations and the saved truth record: 48 readings held at 27.0 W/m², observation IDs matching the truth exactly.
  - PR 89.3% → 174.9%. Basic `CHECKS_PASSED`; improved `REVIEW_REQUIRED` (`POA_STALE`, 49 readings).
  - Two-series palette validated with the dataviz validator (all checks pass).
  - Rendered with system Python and matplotlib 3.11.2; the frozen environment is untouched and no detector was rerun.
- **Compact results table:** `presentation/results_table.md` and `results/phase8/results_table.csv`.
- **Executive memo:** `presentation/executive_memo.md`.
- **Walkthrough:** `presentation/walkthrough.md`.
- **README:** rewritten around the reporting problem, the figure, results and limitations.
- **Packaging validation:** `src/validate_phase8.py`, 11/11 PASS (`results/phase8/packaging_validation.json`).
  - It covers number agreement with the claim register, units, forbidden wording, disclosure, primary/secondary separation and length.
  - One false positive ("daylight-saving" matched the savings pattern) was fixed in the validator's pattern, not in the documents.

### D42. Presentation revision after external review (2026-10-27)
- **Presentation only:**
  - figure title and caption;
  - memo shortened to 425 words, with no unverified one-page claim;
  - case/window/unique-day terminology fixed;
  - PR explanation reworded;
  - the framing sentence made prominent;
  - outcome categories defined;
  - PR plausibility named only as a possible future safeguard.
- **Claim register:** 33 shared claims keep identical values; C17–C19 added.
  - C18 was first computed wrongly by a text search that matched "zero-output plausibility" (an input check). It now inspects rule-table reason codes and confirms no PR-value rule exists.
- **Packaging validation:** 12/12 PASS.
  - The required-phrase check now ignores Markdown emphasis and letter case. Before that fix it flagged the bolded framing sentence and a capitalised "Unconfirmed".
- **Intact:** run1 outputs (11/11) and freeze B. Details in `docs/revision_log.md`.
