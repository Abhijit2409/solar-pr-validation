"""Build detector input from the Phase 2 tables, keeping lineage outside the detector.

The detector sees only evidence.DETECTOR_INPUT_COLUMNS. obs_ref values are sequential
opaque labels assigned after sorting by signal and time, so they carry no source file,
row or fault information. The lineage map (obs_ref -> observation_id, source_file,
source_row) is returned separately for traceability.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as E  # noqa: E402

EXCLUDE_STATES = {"EXCLUDED_BY_SCOPE", "EXPECTED_ROW_MISSING"}


def assign_refs(frame, prefix="r"):
    frame = frame.sort_values(["signal", "interval_start_utc", "_tiebreak"], na_position="last",
                              kind="mergesort").reset_index(drop=True)
    frame["obs_ref"] = [f"{prefix}{i:07d}" for i in range(len(frame))]
    return frame


def load_detector_input(processed, start_local_date, end_local_date, tz):
    """Observations whose interval falls in local dates [start, end); unplaced (time-ambiguous) rows
    are included when their original local date is in range."""
    obs = pd.read_parquet(processed / "observations.parquet",
                          columns=["observation_id", "signal", "interval_start_utc", "standardized_value",
                                   "standardized_unit", "observation_state", "timestamp_original", "source_file",
                                   "source_row"])
    obs = obs[~obs["observation_state"].isin(EXCLUDE_STATES)]
    lo = pd.Timestamp(start_local_date).tz_localize(tz).tz_convert("UTC")
    hi = pd.Timestamp(end_local_date).tz_localize(tz).tz_convert("UTC")
    placed = obs["interval_start_utc"].notna() & (obs["interval_start_utc"] >= lo) & (obs["interval_start_utc"] < hi)
    orig_date = obs["timestamp_original"].str.slice(0, 10)
    unplaced = obs["interval_start_utc"].isna() & (orig_date >= start_local_date) & (orig_date < end_local_date)
    sel = obs[placed | unplaced].rename(columns={"standardized_value": "value", "standardized_unit": "unit"})
    sel = sel.assign(_tiebreak=sel["observation_id"])
    sel = assign_refs(sel)
    lineage = sel[["obs_ref", "observation_id", "source_file", "source_row"]].copy()
    return sel[E.DETECTOR_INPUT_COLUMNS].copy(), lineage
