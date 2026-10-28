# Three-minute walkthrough

*Speaking notes for an analyst interview or a COO conversation. About 480 words, roughly three minutes at a conversational pace.*

---

**The problem (30 s).**
"When an analyst prepares a solar performance report, a key number is the Performance Ratio. It compares the energy produced with what the measured sunlight and installed capacity imply under reference conditions. If the irradiance sensor freezes, drops readings or runs on the wrong clock, the denominator changes even though the plant's real output doesn't. So the decision I wanted to support is simple: report this day's PR, send it to review, or withhold it."

**How I structured the data (40 s).**
"I used one public site from NREL's PVDAQ database, an 893-kilowatt array in California. First I built a data layer where every value traces back to its source file and row. I kept missing, null, zero, invalid and time-ambiguous readings separate, and I handled the daylight-saving hours explicitly instead of guessing. PR is only calculated over intervals where power and all three five-minute irradiance readings are correctly paired. The point is to stop unsupported calculations and keep every reported figure traceable."

**The before-and-after example (30 s).**
"Here's one synthetic case from January 2023. I froze the irradiance reading at 27 watts per square metre for four hours. Power didn't change, but PR went from 89 to 175 percent. The basic checks look at input quality and completeness, and they have no PR plausibility rule, so they passed it. The improved checks saw 49 identical readings and sent it to review. The example shows stale-sensor detection; I'm not suggesting every analyst would publish 175 percent."

**The comparison (40 s).**
"I froze the thresholds and the test windows first, then ran 672 synthetic fault cases on 96 sampled windows of later data, and re-checked the scoring with a separate script.
- Irradiance flatlines of 30 minutes or longer: the improved method caught all 112 cases, which is 28 windows at four severities. Basic caught none.
- One-hour data gaps: improved caught 15 of 26 and basic 8.
- Very short faults, 15-minute flatlines and 30-minute gaps, both methods missed."

**The timing limitation (25 s).**
"Clock shifts are where it fell short. The timing check needs a clear day to compare against modelled sunlight, and that was rarely available: about 2 percent of winter cases and 42 percent in autumn. So 113 of 168 shifted cases stayed inconclusive and were still reported, with PR off by up to 5.4 percentage points. I didn't loosen the rule after seeing that, because that would have meant tuning on the test data."

**Next step (15 s).**
"Next, I'd test on more sites with labelled real sensor incidents. I'd also evaluate a revised timing rule, and possibly a PR plausibility safeguard, on fresh data. This is one site, public data and synthetic faults. It shows where the method works; it isn't a production claim."
