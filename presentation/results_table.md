# Compact results table (run1, primary scenarios)

A case is one fault scenario and severity applied to one window (a sampled unique day). Detected = the fault-specific check withheld the case, out of cases whose unmodified window passed (headline denominator; inconclusive cases stay in it). One site (PVDAQ 2107), public data, synthetic faults. Severity, season and secondary-scenario detail: [evaluation results](../docs/evaluation_results.md).

| Tested fault (primary) | Cases | Basic | Improved | Note |
|---|---|---|---|---|
| Irradiance flatline, 15 min | 28 windows x 1 | 0/28 | 0/28 | Both miss: 4 identical readings is below the 6-reading minimum |
| Irradiance flatline, 30-240 min | 28 windows x 4 severities | 0/112 | 112/112 | Correlated severities; not 112 independent days |
| Missing daytime POA, 30 min | 26 windows x 1 | 0/26 | 0/26 | Both miss |
| Missing daytime POA, 60 min | 26 windows x 1 | 8/26 | 15/26 | Improved adds midday-weighted coverage |
| Missing daytime POA, 120 and 240 min | 26 windows x 2 severities | 52/52 | 52/52 | Both catch |
| POA timestamp shift, +/-15/30/60 min | 28 windows x 6 shifts | 0/168 recognised; 22 withheld for other reasons | 29/168 recognised; **113 inconclusive cases, still reported**; 26 withheld for other reasons | Timing evidence rarely conclusive; still-reported inconclusive cases changed PR by up to 5.4 percentage points |

Unconfirmed alerts across 1,270 unique eligible days (unmodified): basic 177/1270 (13.9%), improved 177/1270 (13.9%) (no labels; this site only).

Source: `results/phase8/claim_register.csv` (C01-C07, C11).
