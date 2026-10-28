import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ingest  # noqa: E402

SITE = {"latitude": 38.996306, "longitude": -122.134111, "elevation_m": 10.0}


def base_config(start_local, end_local):
    cfg = copy.deepcopy(ingest.load_config())
    cfg["scope"]["start_local"] = start_local
    cfg["scope"]["end_local"] = end_local
    for s in cfg["sources"]:
        s["file"] = f"synthetic/{s['source_id']}.csv"
    return cfg


def local_rows(start, end, step_min, value=100.0, drop=()):
    """Raw text frame with local-clock timestamps; 'drop' removes timestamps (as text)."""
    naive = pd.date_range(start, end, freq=f"{step_min}min", inclusive="left")
    text = [t.strftime("%Y-%m-%d %H:%M:%S") for t in naive]
    text = [t for t in text if t not in set(drop)]
    return pd.DataFrame({"timestamp_original": text, "raw_text": [repr(float(value))] * len(text)})


def fall_back_rows(day, step_min, value=0.0):
    """Local clock rows for a fall-back day where the repeated 01:xx hour appears once (as in PVDAQ)."""
    return local_rows(f"{day} 00:00", pd.Timestamp(day) + pd.Timedelta(days=1), step_min, value)


def spring_rows(day, step_min, value=0.0):
    """Local clock rows for a spring-forward day: the 02:xx hour does not exist."""
    df = local_rows(f"{day} 00:00", pd.Timestamp(day) + pd.Timedelta(days=1), step_min, value)
    return df[~df["timestamp_original"].str.slice(11, 13).eq("02")].reset_index(drop=True)


def finish(frame):
    frame = frame.reset_index(drop=True)
    frame.insert(0, "source_row", np.arange(2, len(frame) + 2, dtype="int64"))
    return frame


def empty():
    return finish(pd.DataFrame({"timestamp_original": pd.Series(dtype=str), "raw_text": pd.Series(dtype=str)}))


def run_tables(cfg, frames):
    raw = {s["source_id"]: finish(frames[s["source_id"]]) if s["source_id"] in frames else empty()
           for s in cfg["sources"]}
    hashes = {s["source_id"]: "test-hash-" + s["source_id"] for s in cfg["sources"]}
    return ingest.build_tables(raw, cfg, SITE, hashes)


@pytest.fixture
def site():
    return SITE
