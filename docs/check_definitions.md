# Check definitions (Phase 5 implementation of `experiment-v2`)

**Code:**
- [src/evidence.py](../src/evidence.py): current-input evidence
- [src/checks_basic.py](../src/checks_basic.py): shared checks and basic method
- [src/checks_improved.py](../src/checks_improved.py): improved-only checks
- [src/detect.py](../src/detect.py): evaluation and status aggregation
- [src/detector_inputs.py](../src/detector_inputs.py): builds detector input from the Phase 2 tables

**Config:** [config/experiment_v2.yaml](../config/experiment_v2.yaml). Amendments: [amendments.md](amendments.md).

**Tests:** [tests/test_detectors.py](../tests/test_detectors.py).

## Detector contract

**Input:** `evaluate(obs, site, meta, metric_cfg, exp_cfg, timing_baseline, dates, methods)`
- `obs`: exactly `obs_ref, signal, interval_start_utc, value, unit`. `obs_ref` is an opaque sequential label; anything else is rejected.
- `site`: coordinates, tilt, azimuth, timezone, capacity.
- `meta`: cadence and Phase 2 screening bounds per signal.
- Frozen metric and experiment configs, and the development timing baseline (−11 min).

**Never passed in:** Phase 2 states or flags, source files or rows, injection labels, scenario names, severities, expected outcomes, or the evaluation-window list. Lineage stays in a separate map returned by the loader.

**Output: one row per check, day and method:**
- `check_id`, `check_class` (shared / basic / improved_only);
- `outcome`: PASS / FLAGGED / INCONCLUSIVE;
- `reason_code`;
- `action`: INSUFFICIENT_DATA / REVIEW_REQUIRED / CONTEXT / NONE;
- `measured`, `threshold`, `unit`;
- `affected_intervals` (UTC starts) and `evidence` (JSON).

**Day result:**
- `status` under the frozen precedence INSUFFICIENT_DATA > REVIEW_REQUIRED > CHECKS_PASSED;
- `reason_codes`, `context_codes`, `limitations`;
- `diagnostic_pr`, plus `reported_pr`, which is null unless the day is CHECKS_PASSED.

**Visible limitations on passed days:**
- Basic: "timing, stale values and midday completeness not checked".
- Improved, when the timing check was inconclusive: "timing not verified".

## Evidence recomputed from the supplied rows

Steps per local day:
1. Build the 15-minute meter grid and the three POA starts per meter interval in UTC, from local midnight to the next.
2. Mark the window as sun > 5° at each meter interval's midpoint, and attach clear-sky weights (shared Phase 3 code).
3. Map each row onto its grid start:
   - no row → `EXPECTED_ROW_MISSING`;
   - null value → `SOURCE_NULL`;
   - outside the screening bounds → `INVALID_PHYSICAL`;
   - exactly 0 → `OBSERVED_ZERO`;
   - otherwise `OBSERVED_VALID`;
   - two or more rows → identical (within 1e-6) keeps the first, while differing values give `DUPLICATE_CONFLICT`;
   - rows off the grid are kept as off-grid evidence;
   - rows with no time are counted as unplaced.
4. Exact 3-to-1 pairing and the shared day metrics (Phase 3 `pair_intervals`, `performance_ratio`).

This means removed rows reduce coverage, changed values change the range, zero and stale evidence, and shifted timestamps change alignment, duplicates, geometry and pairing.

## Checks

| ID | Class | Rule (evidence → outcome) | Action / reason |
|---|---|---|---|
| S1 | shared | capacity finite and > 0 | INSUFFICIENT `INVALID_CAPACITY` |
| S5 | shared | ≥ 1 window interval and ≥ 1 paired interval | INSUFFICIENT `NO_WINDOW_INTERVALS` / `NO_PAIRED_DATA` |
| S5b | shared | diagnostic PR finite (when pairs exist) | INSUFFICIENT `PR_UNAVAILABLE` |
| S6 | shared | paired H ≥ 0.5 kWh/m² (INCONCLUSIVE when no pairs) | INSUFFICIENT `LOW_IRRADIATION` |
| B1 | basic | paired ÷ window hours ≥ 0.90 (INCONCLUSIVE when no pairs) | INSUFFICIENT `LOW_TIME_COVERAGE` |
| B2 | basic | no `INVALID_PHYSICAL` meter or POA reading in the window | REVIEW `INVALID_INPUT_IN_WINDOW` |
| B2b | basic | no negative reading in a window interval | REVIEW `NEGATIVE_INPUT_IN_WINDOW` |
| B3 | basic | no window interval with more than one row (identical or conflicting) | REVIEW `UNRESOLVED_TIME_IN_WINDOW` |
| B4 | basic | no off-grid reading inside a window interval | REVIEW `IRREGULAR_SAMPLING` |
| B5a | basic | catch-up (see below), with the spike or its zeros in the window | REVIEW `SUSPECTED_CATCH_UP` |
| B5b | basic | no paired zero with POA mean of the 3 sub-intervals ≥ 200 W/m² | REVIEW `SUSPICIOUS_ZERO_OUTPUT` |
| B5c | basic | zero intervals' share of paired H < 1% | REVIEW `MATERIAL_ZERO_OUTPUT` |
| B5d | basic | no paired interval with a POA sub-interval = 0 and meter-interval clear-sky mean ≥ 200 W/m² | REVIEW `SUSPICIOUS_POA_ZERO` |
| I1 | improved | clear-sky-weighted coverage ≥ 0.90 (INCONCLUSIVE when no pairs) | INSUFFICIENT `LOW_CLEARSKY_WEIGHTED_COVERAGE` |
| I2 | improved | longest stale run < 6 readings | REVIEW `POA_STALE` |
| I3 | improved | conclusive and \|best lag − baseline\| < 8 min | REVIEW `TIMING_INCONSISTENT`; else CONTEXT `TIMING_INCONCLUSIVE` |

**Improved = every basic check + I1, I2, I3.** It does not use Phase 3's flag policy or shared clear-sky threshold. A built-in assertion stops the run if improved ever reports a better status than basic.

**B5a catch-up**, the Phase 2 A-CATCHUP rule recomputed on current inputs: a meter reading above the AC screening bound, directly preceded by at least one contiguous observed zero that is a daylight zero. A daylight zero means elevation > 10° at the midpoint, or any usable POA sub-interval > 200 W/m².

**B5c meaning (A2):** this is the sensitivity of PR to including vs excluding the zero intervals. It is neither a bound on true error nor proof that the readings are faulty.

## Stale values (I2)

- Usable POA readings inside in-window meter intervals, in time order.
- A run continues while the next reading is usable, exactly 5 minutes later, and within 0.05 W/m² of the previous one.
- A run breaks at a missing reading, an unusable (null or invalid) reading, a non-contiguous step, or the window edge.
- **Length is counted in readings.** Elapsed span = readings × 5 min, so 6 readings span 30 min.
- A hold-last flatline of N readings plus the original preceding reading makes N + 1 readings. So a 15-min (3-reading) flatline makes a 4-reading run, below the minimum, and a 30-min flatline makes 7.

## Timing (I3)

1. **Eligible readings:** usable POA readings inside in-window meter intervals, with model mean over [L, L+5) > 50 W/m².
2. **Objective:** maximise the Pearson correlation between measured POA at label L and the 1-minute clear-sky model averaged over [L+k, L+k+5), for k = −90…+90 min.
3. **Tie rule:** among maxima equal to 12 decimals, take the smallest |k|, then the smallest k.
4. **INCONCLUSIVE (CONTEXT, no status change)** if any of:
   - fewer than 60 eligible readings;
   - a constant measured series (correlation undefined);
   - no finite correlation;
   - best lag at ±90;
   - correlation at best < 0.998;
   - measured/model energy ratio outside [0.85, 1.15].
5. **Otherwise:** FLAGGED if |best lag − (−11)| ≥ 8 min.

**Sign convention:** POA timestamps shifted later by s minutes move the best lag by −s.

**Not corrected:** the baseline is a reference, not a correction, and it doesn't claim a cause.

**Latency:** retrospective. The check is available after the reporting day ends, with no multi-day pooling.

## Development reproduction (2018-02-01 to 2020-01-31, 730 days)

| Method | Passed | Review | Insufficient |
|---|---|---|---|
| basic | 574 | 123 | 33 |
| improved | 572 | 123 | 35 |

- **Statuses, reason codes and context codes** equal the Phase 4 prototype on every day for both methods.
- **Shared arithmetic** equals the Phase 3 daily table, with absolute differences ≤ 1e-12.
- **Timing:**
  - inconclusive on 575 days and conclusive on 155, and conclusiveness agrees with the prototype on every day;
  - the best lag differs on 39 days, all inconclusive. The cause is the prototype's eligible-interval gate (sub-interval elevation instead of in-window meter membership, A3).
  - Inconclusive reasons: 538 not clear, 20 at the search boundary, 17 too few intervals.
- **Limitation shown:** 463 improved-passed days carry "timing not verified".
