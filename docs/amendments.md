# Specification amendments

## experiment-v1 → experiment-v2 (Phase 5, 2026-10-27, before any evaluation case existed)

- `experiment-v1` ([config/experiment.yaml](../config/experiment.yaml), [experiment_protocol.md](experiment_protocol.md), `results/phase4/specification_manifest.json`, commit `2952d87`) is **preserved unchanged**.
- `experiment-v2` ([config/experiment_v2.yaml](../config/experiment_v2.yaml)) is what Phase 5–7 code uses.
- **Not changed:** any threshold, period, fault definition, seed, eligibility rule or the 96-window sample.
- **Why amend:** clarifications requested at the start of Phase 5, plus definitions the detector implementation had to pin down. Nothing was tuned on evaluation outcomes; none exist.

| ID | Change | Was (v1) | Now (v2) | Reason |
|---|---|---|---|---|
| A1 | Headline detection rate | headline excluded INCONCLUSIVE from the denominator | **headline includes INCONCLUSIVE**; conclusive-only rate and timing-check applicability are secondary; numerators and denominators always shown | an inconclusive check did not detect the fault, so excluding it would overstate detection |
| A2 | Meaning of zero materiality | "caps the bias … at about 1%" | describes PR **sensitivity** to including vs excluding zero intervals; it does **not** bound true performance error or show the readings are faulty | the earlier wording overclaimed |
| A3 | Timing edge cases | only evidence gates were stated | INCONCLUSIVE also for a best lag at ±90 min (the search boundary) or undefined correlation (constant series) | a boundary maximum cannot establish the lag |
| | | | tie rule: smallest \|k\|, then smallest k | makes results deterministic |
| | | | eligible intervals = POA 5-min intervals inside in-window meter intervals | pins down "in the window" |
| A4 | Catch-up, time/duplicate and sampling evidence | B5a read the Phase 2 A-CATCHUP flag | the same rule, **recomputed from the supplied observations**; B3 and B4 likewise | injected copies must change the evidence |
| A5 | Suspicious POA zero scope | "POA = 0 while clear-sky POA ≥ 200" | evaluated on paired in-window meter intervals, using the meter-interval mean clear-sky POA and any of the three POA sub-intervals exactly 0 | pins down the prototype's actual definition |
| A6 | Stale run counting | "≥ 6 consecutive values (30 min)" | counted in readings, with span = readings × 5 min. Runs break at gaps, unusable readings and window edges. A hold-last block of N readings plus the preceding original reading makes N + 1 | makes the rule unambiguous |
| A7 | Passed day with inconclusive timing | not stated | `CHECKS_PASSED` with `TIMING_INCONCLUSIVE` carries the limitation "timing not verified" | so a passed day is not described as fully verified |

## Effect on development reproduction

- A1, A2 and A7 change reporting only.
- A3–A6 pin down behaviour. Any difference they cause against the Phase 4 prototype is listed in `results/phase5/prototype_comparison.csv` and decision log D29.

## experiment-v2 → experiment-v3 (Phase 6, 2026-10-27, before any evaluation case was generated or run)

- `experiment-v2` ([config/experiment_v2.yaml](../config/experiment_v2.yaml), commit `f1ff436`) and `experiment-v1` are preserved.
- `experiment-v3` ([config/experiment_v3.yaml](../config/experiment_v3.yaml)) is what the Phase 6 injector, evaluator and runner use.
- **Not changed:** any threshold, period, fault severity, seed value, eligibility rule or the 96-window sample.
- **Why amend:** the frozen fault text left these points open, so they are settled here, before any case exists.

| ID | Ambiguity in v1/v2 | Settled as (v3) |
|---|---|---|
| A8 | Which days the detector sees, and where shift collisions can occur | Detector input = local dates [D−2, D+3). Only D is evaluated. The unmodified counterpart and every faulted copy receive the same context. |
| A9 | "Centre … where the block fits", for nested durations | The centre is an in-window meter-interval start c such that [c−120, c+120) is fully in the window. The largest (240 min) block fits, so all smaller nested blocks do too. Block = meter intervals in [c − d/2, c + d/2). Edges lie on the 15-min grid. |
| A10 | Shift block and what a "collision" is | Block = rows whose local date of interval start is in [D−1, D+2). A collision is a shifted row landing on the timestamp of an unshifted row of the same signal. Both rows are kept and counted. Values never change. |
| A11 | Flatline start and "last usable value" | Start t is a POA start inside an in-window meter interval on D, with solar elevation at t + 2.5 min ≥ 15°, and [t, t+240) fully in-window. Hold value = the latest usable POA reading (non-null, within bounds) on day D with start < t. If there is none, the case is UNSCORABLE. |
| A12 | An injection that changes nothing | If zero rows are removed, nulled, shifted or changed in value, the case is UNSCORABLE (`no_effect`). It is never scored as an effective fault. |
| A13 | Seed derivation | One draw per window from `default_rng([family_seed, int(sha256(window_id)[:8])])`. All severities share it, and it doesn't depend on order or batching. |
| A14 | Category precedence and the alert-burden unit | UNSCORABLE > ALREADY_WITHHELD > DETECTED_SPECIFIC > WITHHELD_OTHER > INCONCLUSIVE > MISSED. A method without a fault-specific check for a family can't be INCONCLUSIVE for it. Alert burden counts unique days. |
