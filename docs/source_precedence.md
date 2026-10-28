# Source-precedence policy (Phase 2)

The machine-readable version is `precedence_policy` and `sources` in [config/data_structure.yaml](../config/data_structure.yaml). It is implemented in `resolve_precedence` and `assign_states` in [src/ingest.py](../src/ingest.py).

## Sources and ranks

| Rank | Meter source | POA source | v1 role |
|---|---|---|---|
| 1 | `2107_meter_15m_data.csv` | `2107_irradiance_data.csv` | **primary** (v1 window only) |
| 2 | `2107_meter_15m_data_2024.csv` | `2107_irradiance_data_2024.csv` | excluded increment |
| 3 | `2107_meter_15m_data_2025.csv` | `2107_irradiance_15m_data_2025.csv` | excluded increment |

**Why the ranks are in this order:**
- Rank 1 files define v1.
- The 2024 increment uses the same local-clock convention as the rank 1 files.
- The 2025 increment mixes conventions: the meter is local time and POA is UTC (issue I01).
- Nothing in the change logs says a later release corrects an earlier one.

## Rules

1. **Key:** `sensor_id` + `interval_start_utc`. Rows whose time is ambiguous have no UTC, so they are handled by the DST policy instead.
2. **Winner:** lowest rank first, then lowest source row (file order).
3. **Identical:** values match if they differ by at most 1e-6 in absolute terms, or both are null. The tolerance absorbs float text differences such as `8.199999999999998` vs `8.2`.
4. **Identical duplicates within one file:** the first row keeps its state. Later rows become `DUPLICATE_IDENTICAL` with a `DUPLICATE_TIMESTAMP_IDENTICAL` flag.
5. **Conflicting rows of equal rank:** **no value is chosen**. Every row becomes `DUPLICATE_CONFLICT`, the expected interval becomes `CONFLICT_UNRESOLVED`, and each row gets a `DUPLICATE_TIMESTAMP_CONFLICT` flag.
6. **Lower-rank rows:**
   - identical rows are flagged `OVERLAP_IDENTICAL_ACROSS_FILES`;
   - conflicting rows are flagged `CONFLICTING_OVERLAPPING_SOURCE`;
   - they keep `DUPLICATE_IDENTICAL` / `DUPLICATE_CONFLICT`, unless they're out of v1 scope, in which case `EXCLUDED_BY_SCOPE` takes precedence.
7. **No row is ever dropped.** Each one stays in `observations` with:
   - `source_file`, `source_row` and `source_hash`;
   - `precedence_role` (`unique`, `resolved`, `superseded_identical`, `superseded_conflict`, `conflict_unresolved`);
   - its flags, whose evidence names the winning `observation_id`.
8. **State precedence for a source row:** `EXCLUDED_BY_SCOPE` > `TIME_AMBIGUOUS` > `DUPLICATE_*` > value state (`SOURCE_NULL`, `INVALID_PHYSICAL`, `OBSERVED_ZERO`, `OBSERVED_VALID`). The value-only state is always kept in `value_state`, so an excluded row still shows whether its value was zero, valid, null or invalid.

## Why the 2024–2025 files stay outside v1

- **Mixed timestamp conventions** inside the 2025 increment (I01).
- **Overlap** between the 2024 and 2025 increments (I10).
- **A two-month gap** (Nov–Dec 2023) between them and the primary window.
- **A sampling change** from 5 to 15 minutes for POA in Nov 2024.

Each of these would need its own handling. v1 stays on one continuous, single-convention record (decision_log D5). The increments are still ingested, with full lineage and overlap checks, so a later phase could use them as an extra holdout without re-reading raw files.
