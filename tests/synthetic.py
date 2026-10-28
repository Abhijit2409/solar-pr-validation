"""Synthetic multi-day detector input (no real data): clear-sky POA and proportional meter output.

POA readings are 5-min means of a 1-min pvlib clear-sky POA model for the site (so the timing
lag is 0 by construction); the meter reads 0.8 x 893 kW x POA/1000 (PR exactly 0.8).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

import checks_improved as I
import evidence as E
import ingest

ROOT = Path(__file__).resolve().parents[1]
MCFG = yaml.safe_load((ROOT / "config/metric_definition.yaml").read_text())
EXP3 = yaml.safe_load((ROOT / "config/experiment_v3.yaml").read_text())
META = E.measurement_meta(ingest.load_config())
SITE = {"latitude": 38.996306, "longitude": -122.134111, "elevation_m": 10.0, "tilt_deg": 25.0,
        "azimuth_deg": 180.0, "dc_capacity_kw": 893.0, "local_timezone": "America/Los_Angeles"}
DAY = "2019-06-15"
_CACHE = {}


def days_around(day, lo=-2, hi=3):
    return [(pd.Timestamp(day) + pd.Timedelta(days=k)).strftime("%Y-%m-%d") for k in range(lo, hi)]


def make_obs_days(days, natural_lag=0):
    key = (tuple(days), natural_lag)
    if key in _CACHE:
        return _CACHE[key].copy()
    tz = SITE["local_timezone"]
    start = pd.Timestamp(days[0]).tz_localize(tz).tz_convert("UTC")
    end = (pd.Timestamp(days[-1]) + pd.Timedelta(days=1)).tz_localize(tz).tz_convert("UTC")
    csm = I.ClearSkyMinutes(SITE, start - pd.Timedelta("3h"), end + pd.Timedelta("3h"))
    poa_t = pd.date_range(start, end, freq="5min", inclusive="left")
    poa_v = np.round(csm.mean5(csm.index(poa_t) + natural_lag), 3)
    m_t = pd.date_range(start, end, freq="15min", inclusive="left")
    m_v = np.round(0.8 * 893.0 * poa_v.reshape(-1, 3).mean(axis=1) / 1000, 6)
    df = pd.concat([pd.DataFrame({"signal": "poa_irradiance", "interval_start_utc": poa_t, "value": poa_v,
                                  "unit": "W/m^2"}),
                    pd.DataFrame({"signal": "ac_power", "interval_start_utc": m_t, "value": m_v, "unit": "kW"})],
                   ignore_index=True)
    df.insert(0, "obs_ref", [f"r{i:07d}" for i in range(len(df))])
    _CACHE[key] = df
    return df.copy()
