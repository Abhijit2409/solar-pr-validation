# Evaluation results (Phase 7, run1, `experiment-v3`, freeze B)

This is an independent experiment on public PVDAQ data from one site (system 2107), with synthetic, controlled faults. It is not a measure of production reliability. It doesn't imply any affiliation with Clir Renewables, and it makes no claim about Clir's methods.

## Execution

- **Command:** `.venv/Scripts/python src/run_evaluation.py evaluate --run-id run1`
- **Exit and timing:** process exit code **0**, started 2026-10-27 03:09 UTC, finished 03:15 UTC.
- **Before the run:** freeze B verified, covering 36 files, 5 processed tables and package versions.
- **After the run:** freeze B verified again.
- **Accounting:**

  | Item | Count |
  |---|---|
  | Windows | 96/96 |
  | Detector runs | 768 (96 unmodified + 672 faulted) |
  | Cases | 672/672 planned, attempted and applied |
  | Unscorable | 0 |
  | Failed windows | 0 |
  | Scores | 1,344 (one per case and method) |
  | Population analysis | 1,270 unique days |

  Per scenario: `results/phase7/analysis/case_reconciliation.csv`.
- **Storage:** detector outputs (keyed by opaque `run_key`) and injection truth are in separate files in `results/phase7/run1/`, which is unchanged since completion (hash-checked).

## Primary results

**How to read the table:**
- **Detected:** detected / headline denominator. The denominator is cases minus already-withheld minus unscorable, and inconclusive cases stay in it (amendment A1).
- **Conclusive-only rates** are secondary and appear in `primary_results.csv`.
- **Severities on the same window are correlated**: 32 windows per family, 28 or 26 per severity once already-withheld windows are removed. Do not pool them into a reliability claim.

| Family | Severity | Windows | Basic detected | Improved detected | Improved inconclusive | Missed (basic / improved) | Withheld other (basic / improved) | Already withheld | Unscorable |
|---|---|---|---|---|---|---|---|---|---|
| flatline_poa | 15 min | 32 | 0/28 | 0/28 | 0 | 28 / 28 | 0 / 0 | 4 | 0 |
| flatline_poa | 30 min | 32 | 0/28 | 28/28 | 0 | 28 / 0 | 0 / 0 | 4 | 0 |
| flatline_poa | 60 min | 32 | 0/28 | 28/28 | 0 | 28 / 0 | 0 / 0 | 4 | 0 |
| flatline_poa | 120 min | 32 | 0/28 | 28/28 | 0 | 28 / 0 | 0 / 0 | 4 | 0 |
| flatline_poa | 240 min | 32 | 0/28 | 28/28 | 0 | 26 / 0 | 2 / 0 | 4 | 0 |
| missing_daytime | 30 min | 32 | 0/26 | 0/26 | 0 | 26 / 26 | 0 / 0 | 6 | 0 |
| missing_daytime | 60 min | 32 | 8/26 | 15/26 | 0 | 18 / 11 | 0 / 0 | 6 | 0 |
| missing_daytime | 120 min | 32 | 26/26 | 26/26 | 0 | 0 / 0 | 0 / 0 | 6 | 0 |
| missing_daytime | 240 min | 32 | 26/26 | 26/26 | 0 | 0 / 0 | 0 / 0 | 6 | 0 |
| timestamp_shift | -60 min | 32 | 0/28 | 6/28 | 12 | 22 / 0 | 6 / 10 | 4 | 0 |
| timestamp_shift | -30 min | 32 | 0/28 | 5/28 | 23 | 28 / 0 | 0 / 0 | 4 | 0 |
| timestamp_shift | -15 min | 32 | 0/28 | 5/28 | 23 | 28 / 0 | 0 / 0 | 4 | 0 |
| timestamp_shift | +15 min | 32 | 0/28 | 4/28 | 21 | 25 / 0 | 3 / 3 | 4 | 0 |
| timestamp_shift | +30 min | 32 | 0/28 | 4/28 | 19 | 23 / 0 | 5 / 5 | 4 | 0 |
| timestamp_shift | +60 min | 32 | 0/28 | 5/28 | 15 | 20 / 0 | 8 / 8 | 4 | 0 |

## Main findings

**Flatlined POA**
- Basic recognised **none** of the 140 primary flatlines. Two of them (240 min) were withheld through zero-output rules.
- Improved recognised **every flatline of 30 min or longer (112/112)**, through `POA_STALE`.
- Both methods missed all 28 **15-minute** flatlines, as specified in advance: a 15-minute flatline makes 4 identical readings, below the 6-reading minimum.

**Missing daytime POA**
- **120 and 240 min:** both methods detected all 26/26.
- **30 min:** both missed all 26/26.
- **60 min:** basic detected **8/26** and improved **15/26**. The 7 extra are midday-sensitive (clear-sky-weighted) coverage catching gaps that unweighted coverage misses.

**Timestamp shift (POA only)**
- **Basic** has no timing check, so it recognised **none (0/168)**. It withheld 22 cases through other rules: zero-output checks and low irradiation, because dawn or dusk zeros moved into the window.
- **Improved** recognised **29/168** (4–6 per severity). **113/168 were inconclusive** and 26 were withheld through other reasons. Improved had no outright misses.
- **The limiting factor is timing applicability, not lag estimation.** See the timing section below.

**Secondary scenarios** (reported separately; the rows are fixed 600 W/m² flatline, explicit nulls and whole-file shifts):

| Family | Severity | Windows | Basic detected | Improved detected | Improved inconclusive | Missed (basic / improved) | Withheld other (basic / improved) | Already withheld | Unscorable |
|---|---|---|---|---|---|---|---|---|---|
| flatline_poa | 120 min | 32 | 0/28 | 28/28 | 0 | 28 / 0 | 0 / 0 | 4 | 0 |
| missing_daytime | 120 min | 32 | 26/26 | 26/26 | 0 | 0 / 0 | 0 / 0 | 6 | 0 |
| timestamp_shift | -60 min | 32 | 0/28 | 6/28 | 16 | 26 / 0 | 2 / 6 | 4 | 0 |
| timestamp_shift | -30 min | 32 | 0/28 | 5/28 | 23 | 28 / 0 | 0 / 0 | 4 | 0 |
| timestamp_shift | +30 min | 32 | 0/28 | 4/28 | 24 | 28 / 0 | 0 / 0 | 4 | 0 |
| timestamp_shift | +60 min | 32 | 0/28 | 5/28 | 19 | 25 / 0 | 3 / 4 | 4 | 0 |

The secondary results mirror the primary ones. Explicit nulls behave exactly like removed rows. Whole-file shifts are recognised only on clear days.

## Timing applicability (improved method, shifts)

**Conclusive timing on the faulted day**, as a share of scorable cases (`timing_applicability_by_season.csv`):

| Season | POA-only shift | Whole-file shift |
|---|---|---|
| DJF | 1/42 (2%) | 1/28 (4%) |
| MAM | 6/48 (13%) | 4/32 (13%) |
| JJA | 2/30 (7%) | 1/20 (5%) |
| SON | 20/48 (42%) | 14/32 (44%) |

**Why so few days are conclusive:**
- 7 of the 8 summer shift windows were not clear days by the frozen gate on their **unmodified** counterpart: correlation 0.86–0.996 against the required ≥ 0.998, and some energy ratios of 0.61–0.69.
- **The lag estimate itself tracked every injected shift**, even on inconclusive days. On one window the best lag moved from −10 to −71 min for +60 and to +50 for −60. The clear-day gate is what withheld a verdict.
- **On the 5 windows whose counterpart was conclusive,** 43 of 50 faulted copies were flagged `TIMING_INCONSISTENT`. The other 7 tipped below the gate, by at most 0.0008 in correlation, because those counterparts sat just above 0.998.
- **The gate was set on development data before evaluation and was not changed.** This result shows it is conservative and seasonally limited (issues I24, I26).

## Supplementary common-subset view

This restricts both methods to cases whose unmodified counterpart passed **both** methods.

**Result:** in run1 the already-withheld windows were identical for both methods, so the common subset equals the official denominators and nothing changes (`common_subset_results.csv`). This is supplementary only; the official scoring is the frozen one above.

## PR effect

- **Units:** percentage points (pp), faulted − unmodified diagnostic PR. The unmodified PR is a comparison reference, not the true PR.
- **Relative change** is reported only where the reference is finite and positive.
- **No case had unavailable PR.**

| Faulted cases still reported (CHECKS_PASSED), primary | Cases | \|Δ\| ≥ 1 pp | \|Δ\| ≥ 5 pp | Max \|Δ\| (pp) |
|---|---|---|---|---|
| Basic, flatline | 138 | 69 | 33 | 85.6 |
| Improved, flatline (15 min only) | 28 | 3 | 0 | 2.7 |
| Basic, missing | 44 | 7 | 0 | 2.2 |
| Improved, missing | 37 | 6 | 0 | 2.2 |
| Basic, shift | 146 | 30 | 1 | 5.4 |
| Improved, shift (inconclusive) | 113 | 20 | 1 | 5.4 |

**Highlights:**
- **Basic flatlines:** median |Δ| per severity rises from 0.07 pp (15 min) to 13.2 pp (240 min). The largest was +85.6 pp, where PR went from 0.893 to 1.749 on a 240-minute hold-last that froze a lower value.
- **Improved's missed cases** moved PR by at most 2.7 pp, all from 15-minute flatlines.
- **The PR change can go either way.** A flatline or shift can push PR up or down depending on where it lands in the day.

## Reporting coverage and unconfirmed alert burden (unmodified data)

| Population | Method | Unique days | Not passed (unconfirmed alerts) | Coverage retained |
|---|---|---|---|---|
| Sampled windows | basic | 96 | 14 (14.6%) | 85.4% |
| Sampled windows | improved | 96 | 14 (14.6%) | 85.4% |
| All eligible evaluation days | basic | 1,270 | 177 (13.9%: 165 review, 12 insufficient) | 86.1% |
| All eligible evaluation days | improved | 1,270 | 177 (13.9%: 165 review, 12 insufficient) | 86.1% |

**The improved checks added no burden on unmodified evaluation days.** All alerts come from shared and basic rules, mainly the zero-output policy. They are unconfirmed alerts, not verified false positives.

## Representative cases (selection rules in `src/analyze_phase7.py`)

**1. Largest |PR change| among improved misses** (primary)
- Case: `flatline_poa-DJF-01|primary:hold_last|+15`, 2021-02-01.
- A 15-minute (3-reading) hold-last. `POA_STALE` did not fire because the run was 4 readings.
- PR 0.817 → 0.791 (−2.7 pp), still reported, and timing inconclusive.
- **Rule 5** (short flatline below the threshold) selects this same case.

**2. Caught by improved, missed by basic**
- Case: `flatline_poa-DJF-07|primary:hold_last|+240`, 2023-01-14.
- 48 readings held at one value.
- Improved: `POA_STALE` → review. Basic: still reported with PR 0.893 → 1.749 (+85.6 pp).

**3. Timing inconclusive**
- Case: `timestamp_shift-DJF-02|primary:poa_only|+60`, 2021-01-23.
- 702 rows shifted, 4 collisions.
- Not a clear day, so timing was inconclusive. Still reported with PR +5.4 pp.

**4. Already withheld before injection**
- Case: `flatline_poa-JJA-06|primary:hold_last|+120`, 2022-08-22.
- The counterpart was already `REVIEW_REQUIRED` for `MATERIAL_ZERO_OUTPUT`.
- The fault added `POA_STALE`, but the case is scored `ALREADY_WITHHELD`, not as a detection.

**5. Withheld through other reasons**
- Case: `timestamp_shift-DJF-01|primary:poa_only|-60`, 2020-02-22.
- Moving POA an hour earlier left morning meter zeros paired with daylight POA, which triggered `MATERIAL_ZERO_OUTPUT` and `SUSPICIOUS_ZERO_OUTPUT`.

These are synthetic faults, so no operational root cause is inferred.

## Independent scoring audit

This was an **AI-assisted audit**. No human review was performed.
- `src/audit_phase7.py` re-derives every category, rate, denominator, unique count, PR change and alert-burden figure from the saved detector outputs and truth. It uses its own simple logic and its own reason table taken from the protocol, and does not import the scorer.
- Result: **26/26 items PASS, 0 category mismatches, 0 PR mismatches** (`results/phase7/analysis/scoring_audit.csv`).
- No zero-denominator rate occurred in run1. The null handling is covered by the unit tests.

## Limitations

- **One site, public data, synthetic faults.**
  - The windows are eligible public-data windows, not verified fault-free days.
  - Severities are correlated within windows, at 26–32 windows per cell.
- **Not unseen data.** The evaluation period was profiled structurally in Phases 1–2.
- **Timing check rarely applicable.** It reached a verdict in only 2–44% of cases depending on season, and its clear-day gate is near-threshold for some days.
- **Short faults are invisible to both methods:** 15-minute flatlines and 30-minute gaps.
- **Unconfirmed alerts.** The alerts on unmodified days (13.9%) have no labels, and they come from the zero-output policy, whose thresholds were set after inspecting development data (disclosed in Phase 4).
