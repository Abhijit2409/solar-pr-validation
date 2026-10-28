"""Timestamp conversion, DST handling, expected UTC grids and solar geometry.

Policy (docs/timestamp_policy.md):
- Original timestamp text is kept unchanged; parsing is strict.
- Local timestamps are localised to the site timezone. Times that do not exist
  (spring-forward gap) are NONEXISTENT. Times that occur twice (fall-back hour)
  are AMBIGUOUS, however often the same text appears in the file: occurrence
  count and file order are not evidence of the UTC offset.
- UTC is used internally. Intervals are half-open [start, end).
"""
import numpy as np
import pandas as pd
import pvlib

TIME_OK = "OK"
TIME_AMBIGUOUS = "AMBIGUOUS"
TIME_NONEXISTENT = "NONEXISTENT"
TIME_UNPARSEABLE = "UNPARSEABLE"
ORIGINAL_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_original(text):
    """Strictly parse original timestamp text to naive datetimes (NaT if unparseable)."""
    return pd.to_datetime(pd.Series(text), format=ORIGINAL_FORMAT, errors="coerce")


def convert(text, convention, tz):
    """Convert original timestamp text to local and UTC timestamps.

    Returns a DataFrame aligned with `text` with columns timestamp_local,
    timestamp_utc, time_status and time_note.
    """
    text = pd.Series(text).reset_index(drop=True)
    naive = parse_original(text)
    out = pd.DataFrame({"timestamp_local": pd.Series(pd.NaT, index=text.index, dtype=f"datetime64[ns, {tz}]"),
                        "timestamp_utc": pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns, UTC]"),
                        "time_status": TIME_OK, "time_note": ""})
    out.loc[naive.isna(), "time_status"] = TIME_UNPARSEABLE

    if convention == "utc":
        utc = naive.dt.tz_localize("UTC")
        out["timestamp_utc"] = utc
        out["timestamp_local"] = utc.dt.tz_convert(tz)
        return out
    if convention != "local":
        raise ValueError(f"unknown timestamp convention: {convention}")

    n = len(naive)
    as_dst = naive.dt.tz_localize(tz, ambiguous=np.ones(n, dtype=bool), nonexistent="NaT")
    as_std = naive.dt.tz_localize(tz, ambiguous=np.zeros(n, dtype=bool), nonexistent="NaT")
    nonexistent = naive.notna() & as_dst.isna() & as_std.isna()
    ambiguous = naive.notna() & as_dst.notna() & (as_dst != as_std)

    # Ambiguous fall-back times are never resolved from occurrence count or file order:
    # two identical local timestamps could equally be duplicated readings. Without an
    # explicit UTC offset in the source, every occurrence stays AMBIGUOUS.
    resolved = naive.notna() & ~nonexistent & ~ambiguous
    out.loc[resolved, "timestamp_local"] = as_dst[resolved]
    out.loc[resolved, "timestamp_utc"] = as_dst[resolved].dt.tz_convert("UTC")

    if ambiguous.any():
        count = naive.groupby(naive).transform("size")[ambiguous].astype(int)
        dst_txt = as_dst[ambiguous].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%MZ")
        std_txt = as_std[ambiguous].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%MZ")
        notes = [("repeated fall-back hour appears once;" if n == 1 else
                  f"repeated fall-back hour text appears {n} times in this file with no UTC offset evidence; "
                  "not resolved by order;") + f" UTC candidates {d} (daylight) or {s} (standard)"
                 for n, d, s in zip(count, dst_txt, std_txt)]
        out.loc[ambiguous, "time_status"] = TIME_AMBIGUOUS
        out.loc[ambiguous, "time_note"] = notes
    out.loc[nonexistent, "time_status"] = TIME_NONEXISTENT
    out.loc[nonexistent, "time_note"] = "local time does not exist (spring-forward gap)"
    return out


def ambiguous_candidates_utc(text, tz):
    """For ambiguous local timestamp text, return (daylight, standard) UTC candidates."""
    naive = parse_original(text)
    n = len(naive)
    dst = naive.dt.tz_localize(tz, ambiguous=np.ones(n, dtype=bool), nonexistent="NaT").dt.tz_convert("UTC")
    std = naive.dt.tz_localize(tz, ambiguous=np.zeros(n, dtype=bool), nonexistent="NaT").dt.tz_convert("UTC")
    return dst, std


def local_bound_to_utc(local_text, tz):
    """Convert a local wall-clock bound (must be unambiguous) to UTC."""
    return pd.Timestamp(local_text).tz_localize(tz).tz_convert("UTC")


def expected_grid_utc(start_utc, end_utc, step_minutes):
    """Physical interval starts in UTC, half-open [start_utc, end_utc)."""
    return pd.date_range(start_utc, end_utc, freq=f"{int(step_minutes)}min", inclusive="left")


def local_day_length_hours(local_dates, tz):
    """Actual length in hours of each local calendar day (23, 24 or 25)."""
    d = pd.to_datetime(pd.Series(local_dates))
    start = d.dt.tz_localize(tz)
    end = (d + pd.Timedelta(days=1)).dt.tz_localize(tz)
    return ((end - start).dt.total_seconds() / 3600).to_numpy()


def solar_elevation_deg(times_utc, lat, lon, altitude):
    """Apparent solar elevation (degrees) from pvlib's NREL SPA (numpy) at given UTC times."""
    idx = pd.DatetimeIndex(times_utc)
    pos = pvlib.solarposition.get_solarposition(idx, lat, lon, altitude=altitude)
    return pos["apparent_elevation"].to_numpy()
