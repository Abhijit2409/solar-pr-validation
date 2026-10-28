# Data card: PVDAQ system 2107, "Farm Solar Array"

Phase 1 audit, 2026-10-26. Public data, not "clean" and not ground truth.

## What this site is

A fixed, ground-mounted solar farm near **Arbuckle, California** (38.9963 N, 122.1341 W, 10 m elevation). It is part of NREL's public PVDAQ database and was entered in the 2023 DOE Solar Data Prize.

| Item | Value | How sure |
|---|---|---|
| DC capacity | **893.0 kW** | Verified: 2,880 Hyundai HiS-M310TI modules × 310 W = 892.8 kW |
| Mounting | Fixed, 25° tilt, facing south (180°) | Metadata only |
| Inverters | 24 × ABB TRIO-27.6 string inverters | Metadata; data shows each tops out at ~30.1 kW |
| Plant output ceiling | ~707 kW at the meter | Measured: inverter clipping (24 × ~30.1 kW, less ~2% losses) |
| Timezone | US Pacific, **with daylight saving time** (stored as PST8PDT) | Verified from data |
| Licence | CC BY 4.0 | Stated on the OEDI submission page |

Source: OEDI PVDAQ, DOI [10.25984/1846021](https://doi.org/10.25984/1846021). Each file's URL, hash and access time is in [source_manifest.json](source_manifest.json).

## The two signals PR needs

**1. AC output: plant revenue meter**
- File: `2107_meter_15m_data.csv`, column `meter_revenue_grade_ac_output_meter_149578`.
- **Average AC power in kW** over each 15-minute interval. It is power, not energy: values peak around 700 kW, which matches the plant size. As 15-minute energy they would be impossible.
- **Boundary check:** in every year from 2018 to 2023, and in 2024, the meter reads a median **0.976–0.985 ×** the sum of all 24 inverters' AC power. So the meter measures the whole 893 kW DC system, with about 2% lost in cables and transformers.
- In some years (2018, 2021) the meter and inverter sum agree less closely (correlation 0.76–0.80). Those periods need a look in later phases.
- Most small values come in steps of 0.64 kW, which is probably the meter's resolution. That's an inference: one 2017 night value (0.1216 kW) doesn't fit the pattern.

**2. Irradiance on the panel plane (POA)**
- File: `2107_irradiance_data.csv`, column `poa_irradiance_o_149574`.
- **Average irradiance in W/m²** over each 5-minute interval.
- Sensor type and model are not documented. We assume it sits in the array plane only because of its name. There is no second irradiance sensor to check it against.

**Timestamps**
- `measured_on` is the **local clock**, so it jumps forward one hour in March and back one hour in November.
- Metadata says each timestamp marks the **start** of its interval. That is not independently proven.
- What was tested: POA lines up with the inverters at zero offset in two winters inside 2018–2023 and in 2024. The meter lines up with the inverters at zero offset in 2024; meter timing was not lag-tested in 2017–2023.

## Coverage

| Window | AC meter | POA | Notes |
|---|---|---|---|
| 2017-01 to 2017-10 | yes | **no** | Before POA data and module start date. Out of scope |
| 2017-11 to 2018-01 | yes | **mostly missing** | 69–90% of daytime POA readings absent |
| **2018-02 to 2023-10** | yes | yes | **Primary candidate window (~5.7 years)**. POA daytime missing by year: 2018 10% (Mar 13%, Apr 40%), 2019 0.3%, 2020 0.04%, 2021 1.3%, 2022 8%, 2023 0.1% |
| 2023-11 to 2023-12 | partial / no | no | Gap between files |
| 2024-01 to 2025-12 | yes | yes | Later increments; mixed timestamp conventions (see below) |

The development/evaluation split is decided in Phase 4, not here.

## Problems found, in plain language

- **The 2025 files disagree with each other.** The meter file uses local time, but the POA file in the same release uses UTC. Pairing them without checking would misalign power and sunlight by 7–8 hours. This is a real example of the problem this project is about.
- **The POA sensor has gaps, unevenly spread.** Some years are almost complete (2019, 2020, 2023). Others are not: Nov 2017–Jan 2018 is mostly empty, Apr 2018 is 40% missing, and 2022 is 8% missing. The longest gap is 16.9 days (Feb–Mar 2022).
- **Some sunny periods show zero output.** 43 stretches (343 × 15 min) have the meter at 0 kW while irradiance is above 200 W/m². The cause is unknown.
- **One catch-up spike.** On 2018-02-18 the meter shows two zeros, then 1,783.68 kW, which is twice the DC capacity. This looks like stored readings being released at once, not real output.
- **POA tops out at exactly 1400 W/m²** on 11 readings, which is probably a sensor or logger limit.
- **Clock changes.** One night-time hour each November is missing or merged. Whether this affects PR depends on Phase 3's reporting window and its treatment of night-time intervals.
- **Small timing uncertainty.** The channels line up with each other, but typical POA timing is up to ~20 minutes later than calculated solar noon. The cause (weather pattern, orientation or labelling) is not settled. Very small timestamp shifts may be hard to tell apart from this.

The full list with evidence is in [issue_register.csv](issue_register.csv).

## What this card does not claim

- That any period is free of faults.
- That the POA sensor is accurately calibrated.
- That the reported metadata dates are correct (they conflict; see I09).
- Anything about Clir Renewables' data or methods.
