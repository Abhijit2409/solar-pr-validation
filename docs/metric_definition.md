# Metric definition: daylight-window Performance Ratio (Phase 3, `phase3-v1`)

This was written before any real PR value was computed. It is configured in [config/metric_definition.yaml](../config/metric_definition.yaml) and implemented in [src/pair_intervals.py](../src/pair_intervals.py) and [src/metrics.py](../src/metrics.py).

## Formula

**PR = (E_AC / P_DC_STC) / (H_POA / G_STC)**

| Symbol | Meaning | Unit |
|---|---|---|
| E_AC | AC energy from the plant revenue meter, summed over the **fully paired** intervals of the window | kWh |
| P_DC_STC | DC nameplate capacity: **893.0 kW** (`site_metadata.dc_capacity_kw`) | kW |
| H_POA | Plane-of-array irradiation, summed over the **same** paired intervals | kWh/m² |
| G_STC | Irradiance at standard test conditions, 1 kW/m² | kW/m² |

- PR is stored as a **ratio** (e.g. 0.80) and multiplied by 100 only for display ("80.0%").
- It is **not temperature-corrected**.
- Per interval:
  - E_AC = meter_average_kW × interval_hours
  - H_POA = Σ(POA_W/m² × subinterval_hours) / 1000

  Interval hours come from the actual interval boundaries (end − start). Unit factors are explicit: W/kW/MW for power, and W/m² or kW/m² for irradiance.

**Capacity, 893.0 vs 892.8 kW.** The metadata `power(kW DC)` is 893.0 kW. The module inventory gives 2,880 × 310 W = 892.8 kW, which is 0.02% lower and probably rounded. v1 uses **893.0 kW everywhere** and never switches. The resulting bias is 0.02%, far below any decision threshold. A missing, non-finite or non-positive capacity makes PR unavailable (`INVALID_CAPACITY`).

## System boundary
- Output side: the revenue-grade plant meter, which sees all 24 inverters. In Phase 1 the meter/inverter-sum ratio was 0.976–0.985 per year.
- Capacity side: the whole 893 kW DC array.
- Irradiance side: the single POA sensor. It is assumed to sit in the array plane (25°, 180°), but this was not verified (issue I12).

## Reporting period and window
- **Reporting period:** one local reporting day (America/Los_Angeles). Each 15-min meter interval belongs to the local date of its start. Days are 23, 24 or 25 hours long; the window is defined per interval, so the day length doesn't change the formula.
- **Reporting window (provisional):** a meter interval is in the window if the sun's apparent elevation at the interval midpoint is **above 5°**. This is computed with pvlib from site coordinates and the interval-start assumption.
  - The window comes from **solar geometry**, never from the POA sensor, which may itself be missing or flatlined.
  - The result is therefore a **daylight-window PR**. Energy, irradiation and coverage all use the same window.
- **Night:** always outside the window. That includes the DST-ambiguous fall-back 01:xx intervals, so they cannot affect this PR. The reconciliation checks that no window interval is DST-ambiguous. Night-time energy (the meter is never negative in this data) is not part of the metric.
- **Dawn and dusk:** intervals at or below 5° are excluded. The timing discrepancy of about 11 minutes shifts elevation by only ~1–2° near the horizon, so the window edges can be off by about one interval.

## Pairing
For each meter interval [t, t+15) inside the window:
1. The three POA expected intervals starting at **t, t+5 and t+10** must exist and tile [t, t+15) exactly. Otherwise the interval is excluded as `POA_MISALIGNED`.
2. The meter observation must be usable: `OBSERVED_VALID` or `OBSERVED_ZERO`.
3. **All three** POA observations must be usable. If any one is missing, invalid, ambiguous or conflicting, the **whole meter interval** is excluded from **both** E_AC and H_POA.
4. Nothing is interpolated, forward-filled, scaled up from partial POA, or substituted with clear-sky values.
5. Each paired interval keeps its meter `observation_id` and three POA `observation_id`s.

### Exclusion reasons, one per meter interval

The first reason that applies wins:
1. `OUTSIDE_WINDOW`
2. **Meter problems:**
   - `METER_MISSING`: expected row missing
   - `METER_INVALID`: `INVALID_PHYSICAL`, e.g. the 1,783.68 kW catch-up spike
   - `METER_SOURCE_NULL`
   - `METER_TIME_AMBIGUOUS`
   - `METER_CONFLICT`
3. **POA problems:**
   - `POA_MISSING`
   - `POA_INVALID`
   - `POA_TIME_AMBIGUOUS`
   - `POA_CONFLICT`
   - `POA_MISALIGNED`

Secondary POA problems on meter-excluded intervals are still recorded in the `poa_issue` column. **Invalid input** (`METER_INVALID`, `POA_INVALID`) is kept as a separate reason from a legitimate **zero-output observation**, which stays paired.

## Values with unresolved meaning
- **Unexplained daylight meter zeros** stay `OBSERVED_ZERO`. They are paired and **included** in diagnostic energy as 0 kWh, with their flags. Their cause is not inferred.
- **Only zeros that carry the Phase 2 `DAYLIGHT_ZERO_UNRESOLVED` flag withhold a day.** That flag fires above 10° solar elevation, or when POA exceeds 200 W/m² in the same interval. The window starts at 5°, so paired zeros between 5° and 10° with POA ≤ 200 W/m² **carry no flag and withhold nothing**. This mismatch between the two thresholds is measured, not hidden (see the Phase 3 outcomes below and issue I23).
- **The catch-up spike** keeps its lineage, but as an invalid value it is excluded from pairs (`METER_INVALID`).
- **POA readings at the 1400 W/m² ceiling** are kept and included. Their flag is context only.
- **The ~11-minute timing discrepancy** is not corrected, and the metadata azimuth stays at 180°.
- **Small negative values** within the Phase 2 screening bounds (AC ≥ −44.65 kW, POA ≥ −20 W/m²) are kept as observed, not clipped. A negative input inside the window sends the day to review. None occur in the data.

## Diagnostic vs reportable
- **`diagnostic_pr`** is calculated over whatever paired intervals exist, provided H_POA > 0 and the capacity is valid. Otherwise it is **null**: never 0 and never infinity.
- **`reported_pr`** equals `diagnostic_pr` only when the day's status is `CHECKS_PASSED`. Otherwise it is null and a reason code says why.

## Eligibility status (Phase 3: calculation/input eligibility only)

Status precedence is **INSUFFICIENT_DATA > REVIEW_REQUIRED > CHECKS_PASSED**. All thresholds are provisional; Phase 4 finalises them.

| Status | Reason codes |
|---|---|
| INSUFFICIENT_DATA | `NO_WINDOW_INTERVALS`, `NO_PAIRED_DATA`, `INVALID_CAPACITY`, `PR_UNAVAILABLE` |
| | `LOW_TIME_COVERAGE`: paired hours / window hours < 0.90 |
| | `LOW_CLEARSKY_WEIGHTED_COVERAGE`: clear-sky POA in paired intervals / in window < 0.90 |
| | `LOW_IRRADIATION`: H_POA < 0.5 kWh/m² |
| REVIEW_REQUIRED | any withholding flag on a window interval (paired or excluded): |
| | `UNRESOLVED_ZERO_OUTPUT` (only zeros carrying the Phase 2 DAYLIGHT_ZERO_UNRESOLVED flag) |
| | `SUSPECTED_CATCH_UP` (A-CATCHUP) |
| | `INVALID_INPUT_IN_WINDOW` (INVALID_VALUE) |
| | `UNRESOLVED_TIME_IN_WINDOW` |
| | `NEGATIVE_INPUT_IN_WINDOW` |
| CHECKS_PASSED | none of the above. This is **not** a guarantee that the PR is accurate. |

All triggered reason codes are kept, even when a higher-precedence status wins.

**Flags that are context only:** `A-CEILING`, `IRREGULAR_INTERVAL` and `MISSING_EXPECTED_INTERVAL`. The last two are already reflected in coverage. Their counts are reported per day.

**Coverage caveat.** High time coverage does not guarantee representative irradiation coverage. That is why the clear-sky-weighted coverage is computed as a second measure. Even so, a day can pass both and still miss a short cloudless midday block. The per-interval exclusions are kept so the later improved checks can examine them.

## Comparing PR values
- **Percentage-point difference:** 100 × (PR_a − PR_ref).
- **Relative change:** 100 × (PR_a / PR_ref − 1). It is defined only when PR_ref > 0.
- Here the "reference" PR is the value on unmodified data, not the site's true performance.

## Calibration vs evaluation
- **Development period:** local dates 2018-02-01 to 2020-01-31 (config `development_period`). Phase 3 real-data examples and every provisional choice above use **only** these dates.
- Dates from 2020-02-01 to 2023-11-01 were not used for metric tuning. They were profiled in Phases 1–2 and are **not** unseen raw data.
- The thresholds above were written down **before** any real PR value was computed. They were not tuned on development results.

## Phase 3 measured outcomes (development period only)

Measured outcomes from `src/run_phase3.py` (details in decision log D20 and `results/phase3/`):

**Reconciliation:**
- 70,080 expected meter intervals; 32,556 in the window;
- 31,709 paired;
- 846 excluded `POA_MISSING`; 1 excluded `METER_INVALID`.

**Days:**
- 515 `CHECKS_PASSED`: reported PR median 0.793, IQR 0.752–0.828;
- 180 `REVIEW_REQUIRED`: almost all unresolved zero output at dawn and dusk (issue I21);
- 35 `INSUFFICIENT_DATA`.

**Unflagged zeros:**
- 542 paired window zeros on 226 days have no daylight-zero flag. 50 of those days are `CHECKS_PASSED`.
- On those 50 days the zeros carry a median 0.3% of paired H. Leaving them out would change PR by at most 1.29 percentage points (a diagnostic comparison only, in `unflagged_zero_days_dev.csv`).

**High PR on bright days:**
- 33 passed days with H ≥ 3 kWh/m² have PR > 0.85, mostly in cooler months.
- Possible contributors include the missing temperature correction, POA sensor under-reading or calibration, and irradiance-dependent effects. None of these was tested here (issue I22).

These are calculation/input eligibility outcomes, not detector results.
