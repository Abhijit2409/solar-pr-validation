# Editorial revision log

`docs/evaluation_results.md` (Phase 7, commit `cabe1fe`) is **preserved unchanged**. The clarifications below apply to how its findings are presented in Phase 8. They do not change any number, threshold, score or run output.

| Date | Phase 7 wording | Phase 8 clarification | Reason |
|---|---|---|---|
| 2026-10-27 | "Improved had no outright misses" (timestamp shifts) | 0 shift cases were scored MISSED, but **113/168 were INCONCLUSIVE and still reported**. Zero MISSED does not mean every shift was detected. | prevent reading it as full timing detection |
| 2026-10-27 | "Improved's missed cases moved PR by at most 2.7 pp" | The 2.7 pp maximum applies only to the **MISSED** category (65 primary cases). Inconclusive shift cases that were still reported moved PR by up to **5.4 pp**. | keep the categories distinct |
| 2026-10-27 | "Improved recognised every flatline of 30 min or longer (112/112)" | 112/112 = **28 eligible windows × 4 correlated severities**, not 112 independent days. | disclose correlation |
| 2026-10-27 | "Improved added no burden on unmodified evaluation days" | This holds **for this site's eligible evaluation population**, and the alerts are unconfirmed (no labels). Don't generalise it to other sites. | scope |
| 2026-10-27 | "Diagnostic PR original" | The unmodified PR is a **comparison reference**, not verified true performance. | reference wording |

Every headline number in the Phase 8 package traces to `results/phase8/claim_register.csv`.

## Presentation revision after external review (2026-10-27)

No experiment, threshold, score, run1 output or freeze-B file changed. Claim values are identical to commit `d13cd47` (33/33 shared claims). Three presentation claims were added: C17 figure title, C18 no PR-value rule, C19 framing sentence.

| Item | Before | After | Reason |
|---|---|---|---|
| Figure title | "A frozen irradiance sensor can make a faulty PR look plausible" | "A frozen irradiance sensor inflated reported PR from 89.3% to 174.9%" | 174.9% would itself invite investigation, so it isn't "plausible" |
| Figure and README caption | no baseline caveat | "The basic method checks input quality and completeness but includes no PR plausibility rule." The example demonstrates stale-sensor detection, not that every competent workflow would publish 174.9%. | context for the baseline |
| Executive memo | 619 words; the README called it a "one-page memo" | 425 words, structured as decision, what was built, example, results, timing limitation, next step | requested length. Page fit was **not** verified by rendering, so no one-page claim is made. |
| Terminology | "113 of the 168 shifted days"; "96 days of later data"; "1,270 unmodified evaluation days" | "113 of 168 shifted cases"; "96 sampled windows"; "1,270 unique eligible days" | a case is a scenario and severity applied to a window; severities aren't independent days |
| PR explanation | "energy produced divided by the energy the sunlight sensor says was available" | "Performance Ratio compares the energy produced with what the measured sunlight and installed capacity imply under reference conditions", plus a note that faulty irradiance changes the denominator while actual output is unchanged | accuracy (the formula in the methodology is unchanged) |
| Framing | results bullets | prominent sentence: "The improved checks caught the tested long irradiance flatlines and additional one-hour gaps. Timing uncertainty still allowed some shifted cases with PR changes up to 5.4 percentage points to be reported." | requested emphasis |
| Outcome categories | implicit | README defines detected, inconclusive (still reported), missed and withheld through other reasons | keep the distinctions explicit |
| PR plausibility | not mentioned | named only as a possible future safeguard that needs separate design and validation. No universal ceiling is assumed, and C18 confirms no PR-value rule exists. | scope |

**Figure review** (visual inspection by the assistant; no human review):
- title on one line, no clipped labels and no overlaps;
- smallest text 9.5 pt;
- units W/m², kW and %, with PR changes in percentage points;
- fault window shaded 11:55–15:55 local, matching truth block 19:55–23:55 UTC.

The first re-render clipped the footer source line; the footer was moved up and re-checked.
