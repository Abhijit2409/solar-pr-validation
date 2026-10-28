# Can automated checks keep faulty sensor data out of a solar performance report?

*Independent experiment: one public NREL PVDAQ site (893 kW), synthetic faults. Not affiliated with Clir Renewables; not a production result.*

## The reporting decision

For each day, an analyst decides whether to report the Performance Ratio (PR), investigate it, or withhold it. PR compares the energy produced with what the measured sunlight and installed capacity imply under reference conditions. If the irradiance sensor fails, the denominator changes even though actual power output does not.

## What was built

- **A traceable data layer.**
  - Source lineage is kept for every value.
  - Missing, null, zero, invalid and time-ambiguous readings are kept separate.
  - Timezone and daylight-saving ambiguity are handled explicitly.
  - PR is calculated only over correctly paired intervals.

  The purpose is to prevent unsupported calculations and to make every reported figure traceable.
- **Two validation methods with identical arithmetic.**
  - **Basic:** checks input quality and completeness.
  - **Improved:** adds midday-weighted completeness, stale-irradiance detection and a clear-sky timing check.
  - Neither includes a PR plausibility rule.

## Before-and-after example

A synthetic fault froze irradiance at 27 W/m² for four hours. PR rose from 89.3% to 174.9% (+85.6 percentage points). The basic checks passed it; the improved checks flagged the stale readings for review.

This demonstrates stale-sensor detection. It does not show that every competent workflow would publish 174.9%.

## Main results

Thresholds and 96 evaluation windows (unique days) were frozen before 672 synthetic cases were run.

| Tested fault | Basic | Improved |
|---|---|---|
| Irradiance flatline ≥ 30 min | 0/112 | **112/112** |
| Missing daytime irradiance, 60 min | 8/26 | **15/26** |
| Timestamp shift ±15–60 min | 0/168 | 29/168, 113 inconclusive |

- **112/112** is 28 windows × four correlated severities, not 112 independent days.
- Both methods missed 15-minute flatlines and 30-minute gaps.
- **Unconfirmed alerts:** across 1,270 unique eligible unmodified days, each method withheld 13.9%. There are no labels to confirm them.

## The timing limitation

The improved checks caught the tested long irradiance flatlines and additional one-hour gaps. **Timing uncertainty still allowed some shifted cases with PR changes up to 5.4 percentage points to be reported.**
- **113 of 168 shifted cases** were inconclusive and still reported.
- The timing check needs a clear-sky day.

## Next validation step

- Test both methods on more sites with labelled real sensor incidents.
- Evaluate any revised timing rule, or a separately designed PR plausibility safeguard, on fresh holdout data before relying on it.
- The findings so far come from one site, public data and synthetic faults.
