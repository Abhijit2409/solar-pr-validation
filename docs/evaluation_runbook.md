# Evaluation runbook (Phase 7, `experiment-v3`, implementation freeze B)

## Commands

All commands run from `D:\solar-pr-validation` with the project environment.

| Purpose | Command | Writes |
|---|---|---|
| Unit and synthetic tests | `.venv/Scripts/python -m pytest -q tests` | nothing |
| Development smoke cases | `.venv/Scripts/python src/run_evaluation.py smoke` | `results/phase6/smoke/` (overwritten) |
| Verify freeze B | `.venv/Scripts/python src/run_evaluation.py verify-freeze` | nothing; exits 1 on any mismatch |
| **Final evaluation (Phase 7 only)** | `.venv/Scripts/python src/run_evaluation.py evaluate --run-id run1` | `results/phase7/run1/` |

## What `evaluate` does

1. **Verifies freeze B** (`results/phase6/implementation_freeze_manifest.json`):
   - every frozen code, config, spec, input and test file;
   - the five Phase 2 processed tables;
   - package versions.

   Any mismatch stops the run before any case is generated.
2. **Refuses** to write into an existing `results/phase7/<run-id>`, whether that run completed or failed. Earlier runs are never overwritten.
3. **Loads** the frozen sample `results/phase4/evaluation_window_sample.csv`: 96 windows, 32 per family.
4. **For each window:**
   - loads detector input for local dates [D−2, D+3) from the Phase 2 tables;
   - runs the unmodified counterpart for day D;
   - for every case of the window's family, injects the fault into a copy and runs the faulted copy;
   - assigns fresh opaque `obs_ref`s each time.

   The detector receives only `obs_ref, signal, interval_start_utc, value, unit`. A window that raises an error is recorded in `failures.csv`, and the run continues. Unscorable cases stay in the outputs.
5. **Saves detector outputs separately from truth:**
   - `detector_day_results.csv` and `detector_check_results.parquet`, keyed by opaque `run_key`;
   - `injection_truth.csv` (case_id ↔ run_key, placement, affected observations, collisions);
   - `unmodified_counterparts.csv`.
6. **Scores** every case for both methods (`case_scores.csv`). It writes `rate_summary.csv` by method × family × scenario, plus severity and season breakdowns. Headline rates keep INCONCLUSIVE in the denominator, and a zero denominator gives null.
7. **Alert burden:**
   - on the unique unmodified sampled days (`alert_burden_sampled.csv`);
   - on all 1,270 structurally eligible evaluation days, unmodified (`population_unmodified_days.csv`, `alert_burden_population.csv`).

   Both are reported as unconfirmed alerts.
8. **Writes** `run_metadata.json`, then the `COMPLETED` marker last.

## Case counts

| Family | Windows | Primary cases per window | Secondary per window | Cases |
|---|---|---|---|---|
| missing_daytime | 32 | 4 (30/60/120/240 min, rows removed) | 1 (explicit nulls, 120 min) | 160 |
| timestamp_shift | 32 | 6 (±15/30/60 min, POA only) | 4 (whole file, ±30/60 min) | 320 |
| flatline_poa | 32 | 5 (hold-last 15/30/60/120/240 min) | 1 (fixed 600 W/m², 120 min) | 192 |

That is 672 cases, each scored for 2 methods, plus 96 unmodified counterparts. Primary and secondary scenarios are summarised separately. Severities on one window are correlated sensitivity tests.

## Interpretation rules (fixed in advance)

- **Headline detection rate** = DETECTED_SPECIFIC / (cases − UNSCORABLE − ALREADY_WITHHELD). Also reported: the conclusive-only rate, timing applicability and the withheld rate, each with its numerator and denominator.
- **PR effect** is in percentage points (faulted − unmodified diagnostic PR). Relative change is reported only for a finite positive reference. Unavailable PR is counted. Daily PR values are never averaged into a period PR.
- **Unmodified alerts** are unconfirmed alerts, not false positives.
- **The improved method is not required to win.** Report no improvement, misses and burden plainly.

## If something fails

- **Freeze mismatch:** do not edit the manifest. Investigate. If a change is genuinely needed, create a new amendment version and a new freeze, and record why.
- **Failed run:** keep its directory. Rerun with a new `--run-id`, and report both runs.
