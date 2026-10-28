# Experiment protocol (Phase 4, `experiment-v1`, specification freeze A)

**Question:** before reporting daily PR, does the improved validation method find injected input faults that the basic method misses, and at what review burden on unmodified data?

The machine-readable version is [config/experiment.yaml](../config/experiment.yaml). The development measurements behind each choice are in `results/phase4/`.

Phase 3 (`config/metric_definition.yaml`, `phase3-v1`) and its results are **preserved unchanged** as the original development run. `experiment-v1` replaces only its eligibility and flag policy for this experiment.

## 1. Data periods

| Period | Local dates, half-open | Use |
|---|---|---|
| Development | [2018-02-01, 2020-02-01) | All value-based tuning and policy comparisons |
| Evaluation | [2020-02-01, 2023-11-01) | Fault injection and scoring in Phases 6–7 only |

**Disclosure:**
- The evaluation period was profiled structurally in Phases 1–2: gaps, zeros, timing, completeness.
- The Phase 2 clear-day timing analysis (V16) used all of 2018–2023, so its −11 min result covered evaluation days.
- Phase 4 inspected only **structural eligibility** in the evaluation period: coverage status, observation state, day length and solar geometry.
- No evaluation PR, PR distribution, flag-based statistic or detector output has been computed.
- The evaluation period is therefore **not unseen raw data**. What has not been seen is method behaviour on it.

## 2. Shared calculation rules vs method checks

**Shared by both methods, unchanged from Phase 3:**
- capacity 893.0 kW, PR formula and units, G_STC;
- the daylight window (sun > 5°) and exact 3-to-1 pairing;
- exclusion of invalid and unavailable inputs, null-not-zero for unavailable PR;
- the minimum denominator H ≥ 0.5 kWh/m²;
- status precedence INSUFFICIENT_DATA > REVIEW_REQUIRED > CHECKS_PASSED.

**How the methods are built:**
- **Improved = all basic checks + three additions.**
- Clear-sky-weighted coverage, which was shared eligibility in Phase 3, is now an **improved-only** check.
- The Phase 2 daylight-zero flag no longer withholds by itself. The zero-output policy below replaces it in **both** methods. This is stated openly, not hidden as a shared safeguard.

### Rule table

"When chosen" means before or after development results were inspected.

| ID | Rule | Class | Input | Threshold | Result if triggered | Rationale | When chosen |
|---|---|---|---|---|---|---|---|
| S1 | Capacity valid | shared | site_metadata | finite, > 0 | INSUFFICIENT `INVALID_CAPACITY` | PR undefined otherwise | before (Phase 3) |
| S2 | Units known | shared | standardized units | kW / W/m² tables | error (no guess) | explicit unit factors | before |
| S3 | Window | shared | solar geometry | elevation > 5° | interval outside window | horizon intervals carry little energy and much timing sensitivity | before (Phase 3) |
| S4 | Exact pairing | shared | Phase 2 streams | all 3 POA usable and aligned | interval excluded (reason kept) | no fill, no scaling | before |
| S5 | Paired data exists | shared | pairs | ≥ 1 pair, PR finite | INSUFFICIENT `NO_PAIRED_DATA` / `PR_UNAVAILABLE` | no PR without inputs | before |
| S6 | Minimum denominator | shared | paired H | ≥ 0.5 kWh/m² | INSUFFICIENT `LOW_IRRADIATION` | low-light PR unstable | before (Phase 3) |
| B1 | Time coverage | basic | paired / window hours | ≥ 0.90 | INSUFFICIENT `LOW_TIME_COVERAGE` | standard unweighted completeness within the PR window | before (Phase 3) |
| B2 | Range | basic | window values | Phase 2 screening bounds; negatives | REVIEW `INVALID_INPUT_IN_WINDOW`, `NEGATIVE_INPUT_IN_WINDOW` | values outside plausible range | before (Phase 2) |
| B3 | Time / duplicates | basic | window states | any ambiguous, conflict or duplicate | REVIEW `UNRESOLVED_TIME_IN_WINDOW` | unresolved interval identity | before |
| B4 | Sampling regularity | basic | window timestamps | on nominal grid (15 / 5 min) | REVIEW `IRREGULAR_SAMPLING` | logger cadence change | before |
| B5a | Catch-up artefact | basic | Phase 2 A-CATCHUP | any in window | REVIEW `SUSPECTED_CATCH_UP` | buffered release | before (Phase 3) |
| B5b | Suspicious meter zero | basic | paired meter + POA | zero while POA mean ≥ 200 W/m² | REVIEW `SUSPICIOUS_ZERO_OUTPUT` | development zero rate falls to ≤ 1.5% above 200 W/m² | **after** (development) |
| B5c | Material zero output | basic | paired zeros | zeros carry ≥ 1% of day's paired H | REVIEW `MATERIAL_ZERO_OUTPUT` | caps the bias from zeros left in a passed day at about 1% relative (about 0.8 pp) | **after** (development) |
| B5d | Suspicious POA zero | basic | POA + clear-sky model | POA = 0 while clear-sky POA ≥ 200 W/m² | REVIEW `SUSPICIOUS_POA_ZERO` | a sensor reading 0 under strong expected sun | **after** (development) |
| I1 | Midday completeness | improved | paired vs window clear-sky POA | ≥ 0.90 | INSUFFICIENT `LOW_CLEARSKY_WEIGHTED_COVERAGE` | losing midday hours matters more than losing dawn | before (Phase 3) |
| I2 | POA stale value | improved | in-window POA | ≥ 6 consecutive values within 0.05 W/m² (30 min) | REVIEW `POA_STALE` | identical readings at 0.1 W/m² resolution are rare in daylight | **after** (development grid) |
| I3 | Timing consistency | improved | POA vs clear-sky model | \|lag − (−11)\| ≥ 8 min on a conclusive day | REVIEW `TIMING_INCONSISTENT`; else `TIMING_INCONCLUSIVE` (context) | stable clear-day baseline (IQR 1.5 min) | evidence rules from Phase 2 (whole record); baseline and threshold **after** (development) |

The basic method is intentionally a competent baseline: coverage inside the PR window, range, time, sampling and zero-output plausibility. It is not a straw man. If basic or shared checks catch a fault, that is reported as such.

## 3. Zero-output policy (issues I21, I23)

It covers the **whole reporting window from 5°**, so it no longer depends on the Phase 2 flag's 10° / 200 W/m² trigger. Three kinds of zero are distinguished:

1. **Recorded zero:** every observed zero is a valid numerical observation and stays in E_AC.
2. **Suspicious zero (review):**
   - B5b: a meter zero while concurrent POA ≥ 200 W/m²;
   - B5c: zeros that together carry ≥ 1% of the day's paired H;
   - B5d: a POA zero under strong clear-sky irradiance.
3. **Confirmed data fault:** never asserted. A review status means "investigate", not "bad data".

**Development evidence** (`results/phase4/dev_zero_rate_by_poa.csv`): the meter zero rate is 24–26% below 50 W/m², 13% at 50–100, 7% at 100–150, 2% at 150–200, and 0.6–1.5% above 200 W/m².
- Zeros at low light recur often, so they are not treated as bad data by themselves.
- Above 200 W/m² zeros are rare: 174 intervals on 15 development days.

**Materiality threshold:** 0.5% / 1% / 2% would send 171 / 123 / 34 development days to review (sensitivity rows in the comparison CSV). **1% was chosen as the stricter of the plausible caps,** because it bounds the bias from zeros left in a passed day. It was **not** chosen for its pass rate.
- On basic-passed development days with any zero, zeros carry at most 0.98% of H.
- PR including vs excluding them differs by a median of −0.29 pp and at most 0.68 pp.

## 4. Development reporting coverage and review burden (730 development days)

| Policy | Passed | Review | Insufficient | Coverage | Review burden |
|---|---|---|---|---|---|
| phase3-v1 (original Phase 3 run) | 515 | 180 | 35 | 70.5% | 24.7% |
| basic, experiment-v1 | 574 | 123 | 33 | 78.6% | 16.8% |
| improved, experiment-v1 | 572 | 123 | 35 | 78.4% | 16.8% |

**Differences on unmodified development data:**
- Improved differs from basic on **2 days**, both `LOW_CLEARSKY_WEIGHTED_COVERAGE`.
- `POA_STALE` fires on 1 day, which was already withheld.
- `TIMING_INCONSISTENT` fires on 0 days, and the timing check is **inconclusive on 575 of 730 days (79%)**.
- So the improved method adds little burden on unmodified data. Whether it adds detection value is what the experiment tests.

These statuses come from a development prototype (`src/phase4_development_analysis.py`). Phase 5 must reproduce them with the tested detectors.

## 5. Improved checks in detail

**I1 midday completeness:**
- Clear-sky-weighted coverage = Σ clear-sky POA × hours over paired intervals ÷ the same sum over window intervals.
- The model supplies weights only, never values.

**I2 stale POA:**
- Usable POA intervals inside the window, in contiguous 5-minute steps.
- A run is a sequence of consecutive values whose successive differences are ≤ 0.05 W/m².
- Flag if a run has ≥ 6 values (30 min).
- Development profile (`dev_stale_run_grid.csv`): days flagged at 0.05 W/m² tolerance were 4 / 2 / 1 / 0 for minimum runs of 3 / 4 / 6 / 9 values. At 1.0 W/m² tolerance, a 6-value minimum would flag 23 days.
- A hold-last flatline of N intervals produces a run of N + 1 values, because it includes the original preceding value.

**I3 timing:**
- Measured 5-minute POA is compared with clear-sky POA averaged over [L + k, L + k + 5) for k = −90…+90 min.
- Eligible intervals: in the window, with model POA > 50 W/m². At least 60 such intervals are needed.
- The day is **conclusive** only if correlation at the best lag is ≥ 0.998 and the measured/model energy ratio is in [0.85, 1.15]. Otherwise the result is `TIMING_INCONCLUSIVE`, which is context only and changes no status.
- **Baseline:** −11 min, the development median over 155 conclusive days (IQR −12 to −10.5).
  - The baseline is a reference, not a correction, and makes no claim about its cause.
  - Injected shift magnitudes are applied as stated and are **not** adjusted by the baseline.
- **Threshold:** |best lag − baseline| ≥ 8 min flags 0 conclusive development days (5 min would flag 2).
- **Sign:** an injected shift of +s (POA timestamps s minutes later) moves the best lag by −s.
- **Latency:** same-day and retrospective. The check runs once the reporting day is complete, with no multi-day pooling.
- **Known limitation:** conclusive days are rare in winter. In development there were 1 in January, 3 in February and none in December.

## 6. Fault scenarios

Faults are injected into **copies** of the Phase 2 resolved streams for days D−1…D+1. Raw files and the processed tables are never modified.
- The detectors receive only the faulted observations, metadata and frozen config, never labels.
- Every case has an **unmodified counterpart**: the same window, evaluated identically.

| Family | Primary scenario | Severities | Placement | Secondary (reported separately) |
|---|---|---|---|---|
| Missing daytime | POA **rows removed** (`EXPECTED_ROW_MISSING`) | 30, 60, 120, 240 min | one seeded centre per window; nested blocks on the 15-min grid inside the window | **explicit nulls** (`SOURCE_NULL`), 120 min |
| Timestamp shift | **POA-only** shift of all POA rows dated D−1…D+1 | −60, −30, −15, +15, +30, +60 min | whole block; evaluated day D | **whole-file** shift (meter + POA): ±30, ±60 |
| Flatline POA | **hold-last** value, which is in range by construction | 15, 30, 60, 120, 240 min | seeded start in-window where elevation ≥ 15° and the block fits | **fixed 600 W/m²**, 120 min |

**Shift magnitudes:**
- 15 min is the meter cadence and 3 POA steps.
- 30 min matches a half-hour logger offset.
- 60 min matches a DST or timezone error.
- Collisions at block edges fall at night outside D's window. They are kept as duplicates, never dropped.

**Expected limits, stated in advance:**
- A 15-minute flatline makes a 4-value run, which is below I2's minimum, so a miss is expected.
- Shifts on inconclusive days are scored `INCONCLUSIVE` for I3.

Seeds: sampling 20261027; injection 4101 / 4102 / 4103.

## 7. Window selection

**Eligibility** is structural only and independent of any detector result. A day must:
- be in the evaluation period;
- not be a 23- or 25-hour DST day;
- have every in-window meter interval, and every POA interval inside it, `COVERED` with `OBSERVED_VALID` / `OBSERVED_ZERO`;
- have neighbouring days with data.

**Result:**
- 1,270 of 1,369 evaluation days are eligible: DJF 284, MAM 349, JJA 317, SON 320.
- Excluded days, where one day can match several reasons: 87 incomplete POA window, 3 incomplete meter window, 7 DST days, 6 missing a neighbour day.
- Known outage periods (I20) are excluded through the completeness rule.
- Days with Phase 2 zero flags are **not** excluded. On those days the unmodified counterpart may already be withheld, and such cases are scored `ALREADY_WITHHELD`.

**Population label:** these are *eligible public-data windows*, not verified fault-free days.

**Sample:**
- 8 windows per family per season, drawn with seed 20261027, disjoint across families.
- That gives **96 unique windows** (`results/phase4/evaluation_window_sample.csv`).
- Severities on the same window are correlated sensitivity tests, not independent cases. The primary scenarios give 32 windows × 4 / 6 / 5 severities = 128 / 192 / 160 cases.

## 8. Scoring

Each case is scored per method (`scoring` in the config):
- `ALREADY_WITHHELD`: the unmodified counterpart is not `CHECKS_PASSED`. It is never counted as a new detection.
- `DETECTED_SPECIFIC`: the faulted status is not passed, with a fault-specific reason:
  - missing: `LOW_TIME_COVERAGE`, `LOW_CLEARSKY_WEIGHTED_COVERAGE`, `NO_PAIRED_DATA`;
  - shift: `TIMING_INCONSISTENT`;
  - flatline: `POA_STALE`.
- `WITHHELD_OTHER`: withheld only through other reasons, i.e. a status change that is not specific to the fault.
- `INCONCLUSIVE`: still passed, and the specific check was inconclusive.
- `MISSED`: still passed.
- `UNSCORABLE`: the injection could not be applied.

**Reported per family, scenario, severity and method,** always with numerators, denominators and unique windows:
- detection rate, with and without inconclusive cases in the denominator;
- withheld rate.

**PR effect:**
- percentage points: 100 × (faulted − unmodified diagnostic PR);
- relative change, only for a finite, positive unmodified PR;
- unavailable PR is counted, never imputed.

**On unmodified data:**
- alert burden = share of eligible days not passed, reported as **unconfirmed alerts**. There is no false-positive rate without verified labels.
- reporting coverage retained = share passed.

**Latency:** same-day, retrospective.

The two methods are compared at their frozen settings. **The improved method is not required to win.**

## 9. Freeze checkpoints

**A. Specification freeze (end of Phase 4, now).** Covers:
- this protocol and `config/experiment.yaml`;
- `config/metric_definition.yaml`;
- the source and processed-data manifests;
- the development analysis outputs;
- the frozen window sample.

Hashes are in `results/phase4/specification_manifest.json`, and a local Git commit records it. Detector, injector and evaluator code **does not exist yet**, so no implementation hash is claimed.

**B. Implementation freeze (after Phases 5–6, before Phase 7).** Covers:
- tested detector, injector and evaluator code;
- configuration and dependency hashes;
- the final input manifest;
- a statement that no evaluation outcome was used for tuning.

**Amendments.** If an implementation defect forces a specification change, the original stays, a new version (`experiment-v2`) is created, and the change is disclosed with its reason.
