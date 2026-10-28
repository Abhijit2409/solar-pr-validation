"""Phase 2 raw-to-standardized pipeline for PVDAQ system 2107.

Reads the raw CSVs listed in config/data_structure.yaml (unchanged), and writes:
  data/processed/site_metadata.parquet
  data/processed/observations.parquet           one row per source row + one per missing expected interval
  data/processed/expected_intervals.parquet     physical UTC timeline for in-scope sensors
  data/processed/structural_quality_flags.parquet
  data/processed/sampling_segments.parquet
  results/phase2/run_manifest.json

No energy, irradiation or PR is calculated. No values are filled or interpolated.

Usage:
    python src/ingest.py [--out-dir DIR] [--results-dir DIR]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standardize as S  # noqa: E402
import timestamps as T  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "data_structure.yaml"
CODE_FILES = ["src/ingest.py", "src/timestamps.py", "src/standardize.py"]

OBS_COLUMNS = [
    "observation_id", "site_id", "sensor_id", "signal", "source_id", "timestamp_original",
    "timezone_original", "timestamp_local", "timestamp_utc", "interval_start_utc", "interval_end_utc",
    "interval_minutes", "sampling_segment_id", "raw_value", "raw_unit", "standardized_value",
    "standardized_unit", "conversion_rule", "source_file", "source_row", "source_hash",
    "observation_state", "value_state", "exclusion_reason", "precedence_role", "time_status", "quality_notes",
]


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_hash(df):
    """Content hash of a table (independent of Parquet file bytes)."""
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()).hexdigest()


def read_source(src, cfg, root=ROOT):
    """Raw source as text: source_row (file line number, header = line 1), timestamp_original, raw_text."""
    column = cfg["sensors"][src["sensor_id"]]["source_column"]
    raw = pd.read_csv(root / src["file"], dtype=str, keep_default_na=False)
    return pd.DataFrame({"source_row": np.arange(2, len(raw) + 2, dtype="int64"),
                         "timestamp_original": raw[src["timestamp_column"]].to_numpy(),
                         "raw_text": raw[column].to_numpy()})


def sampling_segments(naive, source_id):
    """Group calendar months by modal timestamp step; consecutive equal months form a segment."""
    s = naive.dropna().sort_values()
    step = s.diff().dt.total_seconds().div(60)
    month = s.dt.to_period("M")
    modal = step.groupby(month).agg(lambda x: x.mode().iloc[0] if x.notna().any() else np.nan)
    modal = modal.bfill()
    segs, prev = [], None
    for m, st in modal.items():
        if st != prev:
            segs.append({"source_id": source_id, "step_minutes": float(st), "first_month": str(m), "last_month": str(m)})
            prev = st
        else:
            segs[-1]["last_month"] = str(m)
    for i, sg in enumerate(segs):
        sg["sampling_segment_id"] = f"{source_id}_seg{i + 1}"
        in_seg = (month >= pd.Period(sg["first_month"])) & (month <= pd.Period(sg["last_month"]))
        sg["first_timestamp_original"] = str(s[in_seg].min())
        sg["last_timestamp_original"] = str(s[in_seg].max())
        sg["rows"] = int(in_seg.sum())
    return pd.DataFrame(segs)


def source_observations(raw, src, cfg, source_hash):
    """Per-source observation rows with timestamps, units and value states (before precedence)."""
    sensor = cfg["sensors"][src["sensor_id"]]
    tz = cfg["site"]["local_timezone"]
    t = T.convert(raw["timestamp_original"], src["timestamp_convention"], tz)
    raw_num = S.parse_raw_value(raw["raw_text"])
    std, rule = S.standardize(raw_num, sensor["multiplier"], sensor["offset"],
                              sensor["raw_unit"], sensor["standardized_unit"])
    lim = cfg["physical_limits"][sensor["signal"]]
    naive = T.parse_original(raw["timestamp_original"])
    segs = sampling_segments(naive, src["source_id"])
    month = naive.dt.to_period("M").astype(str)
    seg_id = pd.Series(pd.NA, index=raw.index, dtype="object")
    step = pd.Series(np.nan, index=raw.index)
    for _, sg in segs.iterrows():
        m = (month >= sg.first_month) & (month <= sg.last_month)
        seg_id[m], step[m] = sg.sampling_segment_id, sg.step_minutes

    df = pd.DataFrame({
        "site_id": cfg["site"]["site_id"], "sensor_id": src["sensor_id"], "signal": sensor["signal"],
        "source_id": src["source_id"], "precedence_rank": src["precedence_rank"], "v1_role": src["v1_role"],
        "timestamp_original": raw["timestamp_original"].to_numpy(),
        "timezone_original": (f"{tz} local clock (source code {cfg['site']['source_timezone_code']})"
                              if src["timestamp_convention"] == "local" else "UTC"),
        "time_status": t["time_status"].to_numpy(), "time_note": t["time_note"].to_numpy(),
        "naive_original": naive.to_numpy(),
        "interval_minutes": step.to_numpy(), "sampling_segment_id": seg_id.to_numpy(),
        "raw_value": raw["raw_text"].to_numpy(), "raw_unit": sensor["raw_unit"],
        "standardized_value": std.to_numpy(), "standardized_unit": sensor["standardized_unit"],
        "conversion_rule": rule, "source_file": src["file"], "source_row": raw["source_row"].to_numpy(),
        "source_hash": source_hash,
        "value_state": S.value_state(std, lim["min"], lim["max"]).to_numpy(),
    })
    df["timestamp_local"] = t["timestamp_local"]
    df["timestamp_utc"] = t["timestamp_utc"]
    minutes = pd.to_timedelta(df["interval_minutes"], unit="min")
    if cfg["interval_label"] == "start":
        df["interval_start_utc"] = df["timestamp_utc"]
    else:
        df["interval_start_utc"] = df["timestamp_utc"] - minutes
    df["interval_end_utc"] = df["interval_start_utc"] + minutes
    df["observation_id"] = df["source_id"] + ":" + df["source_row"].astype(str)
    return df, segs


def apply_scope(df, cfg):
    tz = cfg["site"]["local_timezone"]
    start_utc = T.local_bound_to_utc(cfg["scope"]["start_local"], tz)
    end_utc = T.local_bound_to_utc(cfg["scope"]["end_local"], tz)
    start_naive, end_naive = pd.Timestamp(cfg["scope"]["start_local"]), pd.Timestamp(cfg["scope"]["end_local"])
    has_utc = df["timestamp_utc"].notna()
    in_window = np.where(has_utc,
                         (df["interval_start_utc"] >= start_utc) & (df["interval_start_utc"] < end_utc),
                         (df["naive_original"] >= start_naive) & (df["naive_original"] < end_naive))
    primary = df["v1_role"] == "primary"
    df["in_scope"] = primary & in_window
    df["exclusion_reason"] = np.where(~primary, "v1 scope: excluded increment release (decision_log D5)",
                                      np.where(~in_window, "v1 scope: outside v1 window", ""))
    return df, start_utc, end_utc


def resolve_precedence(df, tol):
    """Assign precedence_role for rows sharing sensor_id and interval_start_utc."""
    df["precedence_role"] = "unique"
    keyed = df[df["interval_start_utc"].notna()].copy()
    keyed["_order"] = np.arange(len(keyed))
    keyed = keyed.sort_values(["sensor_id", "interval_start_utc", "precedence_rank", "source_row"], kind="mergesort")
    keys = ["sensor_id", "interval_start_utc"]
    g = keyed.groupby(keys, sort=False)
    keyed["dup_n"] = g["source_id"].transform("size")
    dup = keyed[keyed["dup_n"] > 1].copy()
    if dup.empty:
        return df
    g = dup.groupby(keys, sort=False)
    dup["is_first"] = g.cumcount() == 0
    dup["best"] = dup["precedence_rank"] == g["precedence_rank"].transform("min")
    bv = dup["standardized_value"].where(dup["best"])
    gb = bv.groupby([dup[k] for k in keys], sort=False)
    spread = gb.transform("max") - gb.transform("min")
    nan_best = (dup["best"] & dup["standardized_value"].isna()).groupby([dup[k] for k in keys], sort=False).transform("sum")
    num_best = (dup["best"] & dup["standardized_value"].notna()).groupby([dup[k] for k in keys], sort=False).transform("sum")
    best_conflict = (spread > tol) | ((nan_best > 0) & (num_best > 0))
    first_val = dup.loc[dup["is_first"], keys + ["standardized_value"]].rename(columns={"standardized_value": "_win"})
    dup = dup.merge(first_val, on=keys, how="left")
    same = S.values_equal(dup["standardized_value"], dup["_win"], tol)
    role = np.select(
        [best_conflict.to_numpy() & dup["best"].to_numpy(),
         best_conflict.to_numpy(),
         dup["is_first"].to_numpy(),
         same],
        ["conflict_unresolved", "superseded_conflict", "resolved", "superseded_identical"],
        default="superseded_conflict")
    df.loc[dup["observation_id"].map(dict(zip(df["observation_id"], df.index))), "precedence_role"] = role
    # source of the winning record, for flag evidence
    winners = dup.loc[dup["is_first"], keys + ["source_id", "observation_id"]].rename(
        columns={"source_id": "_win_source", "observation_id": "_win_obs"})
    info = dup[["observation_id"] + keys].merge(winners, on=keys, how="left")
    df["_win_source"] = df["observation_id"].map(dict(zip(info["observation_id"], info["_win_source"])))
    df["_win_obs"] = df["observation_id"].map(dict(zip(info["observation_id"], info["_win_obs"])))
    return df


def assign_states(df):
    state = df["value_state"].copy()
    state[df["precedence_role"] == "superseded_identical"] = S.DUPLICATE_IDENTICAL
    state[df["precedence_role"].isin(["superseded_conflict", "conflict_unresolved"])] = S.DUPLICATE_CONFLICT
    state[df["time_status"] != T.TIME_OK] = S.TIME_AMBIGUOUS
    state[~df["in_scope"]] = S.EXCLUDED_BY_SCOPE
    df["observation_state"] = state
    df.loc[df["in_scope"], "exclusion_reason"] = ""
    return df


def expected_intervals(df, segments, cfg, start_utc, end_utc):
    """Physical UTC timeline per in-scope sensor, with coverage status."""
    tz = cfg["site"]["local_timezone"]
    out = []
    for sensor_id in sorted(df.loc[df["in_scope"], "sensor_id"].unique()):
        prim = [s["source_id"] for s in cfg["sources"] if s["sensor_id"] == sensor_id and s["v1_role"] == "primary"]
        segs = segments[segments["source_id"].isin(prim)].reset_index(drop=True)
        bounds = [start_utc]
        for i in range(1, len(segs)):
            first = df[(df["sampling_segment_id"] == segs.loc[i, "sampling_segment_id"]) & df["in_scope"]]
            bounds.append(max(start_utc, first["interval_start_utc"].min()))
        bounds.append(end_utc)
        parts = []
        for i, sg in segs.iterrows():
            grid = T.expected_grid_utc(bounds[i], bounds[i + 1], sg.step_minutes)
            parts.append(pd.DataFrame({"interval_start_utc": grid, "interval_minutes": sg.step_minutes,
                                       "sampling_segment_id": sg.sampling_segment_id}))
        ei = pd.concat(parts, ignore_index=True)
        ei.insert(0, "sensor_id", sensor_id)
        ei.insert(0, "site_id", cfg["site"]["site_id"])
        ei["signal"] = cfg["sensors"][sensor_id]["signal"]
        ei["interval_end_utc"] = ei["interval_start_utc"] + pd.to_timedelta(ei["interval_minutes"], unit="min")

        rows = df[df["in_scope"] & (df["sensor_id"] == sensor_id)]
        resolved = rows[rows["observation_state"].isin(S.RESOLVED_STATES)]
        conflict = rows[rows["precedence_role"] == "conflict_unresolved"]
        amb = rows[rows["time_status"] == T.TIME_AMBIGUOUS]
        dst_c, std_c = T.ambiguous_candidates_utc(amb["timestamp_original"], tz)
        amb_starts = set(dst_c.dropna()) | set(std_c.dropna())
        res_map = dict(zip(resolved["interval_start_utc"], resolved["observation_id"]))
        state_map = dict(zip(resolved["interval_start_utc"], resolved["observation_state"]))
        counts = rows[rows["interval_start_utc"].notna()].groupby("interval_start_utc").size()

        ei["resolved_observation_id"] = ei["interval_start_utc"].map(res_map)
        ei["resolved_observation_state"] = ei["interval_start_utc"].map(state_map)
        ei["n_source_rows"] = ei["interval_start_utc"].map(counts).fillna(0).astype("int64")
        covered = ei["resolved_observation_id"].notna()
        in_conflict = ei["interval_start_utc"].isin(set(conflict["interval_start_utc"]))
        in_amb = ei["interval_start_utc"].isin(amb_starts)
        ei["coverage_status"] = np.select([covered, in_conflict, in_amb],
                                          ["COVERED", "CONFLICT_UNRESOLVED", "DST_AMBIGUOUS"], default="MISSING")
        local = ei["interval_start_utc"].dt.tz_convert(tz)
        ei["local_reporting_date"] = local.dt.strftime("%Y-%m-%d")
        days = pd.Series(ei["local_reporting_date"].unique())
        lengths = dict(zip(days, T.local_day_length_hours(days, tz)))
        ei["local_day_length_h"] = ei["local_reporting_date"].map(lengths)
        grid_set = set(ei["interval_start_utc"])
        df.loc[rows.index, "_on_grid"] = rows["interval_start_utc"].isin(grid_set) | rows["interval_start_utc"].isna()
        out.append(ei)
    cols = ["site_id", "sensor_id", "signal", "sampling_segment_id", "interval_start_utc", "interval_end_utc",
            "interval_minutes", "local_reporting_date", "local_day_length_h", "coverage_status",
            "resolved_observation_id", "resolved_observation_state", "n_source_rows"]
    return pd.concat(out, ignore_index=True)[cols]


def missing_rows(ei, cfg):
    """One generated observation row per MISSING expected interval (no values, no source)."""
    m = ei[ei["coverage_status"] == "MISSING"]
    sensors = cfg["sensors"]
    return pd.DataFrame({
        "observation_id": m["sensor_id"] + ":missing:" + m["interval_start_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site_id": m["site_id"], "sensor_id": m["sensor_id"], "signal": m["signal"], "source_id": pd.NA,
        "interval_start_utc": m["interval_start_utc"], "interval_end_utc": m["interval_end_utc"],
        "interval_minutes": m["interval_minutes"], "sampling_segment_id": m["sampling_segment_id"],
        "raw_unit": m["sensor_id"].map(lambda s: sensors[s]["raw_unit"]),
        "standardized_unit": m["sensor_id"].map(lambda s: sensors[s]["standardized_unit"]),
        "observation_state": S.EXPECTED_ROW_MISSING, "exclusion_reason": "", "precedence_role": "generated",
        "time_status": "", "time_note": "", "in_scope": True,
    })


def build_flags(df, ei, segments, cfg, site):
    """Structural quality flags (no Phase 5 detector decisions)."""
    rules = cfg["flag_rules"]
    flags = []

    def add(rows, flag_type, rule_id, evidence):
        if len(rows) == 0:
            return
        f = pd.DataFrame({"sensor_id": rows["sensor_id"].to_numpy(), "flag_type": flag_type,
                          "observation_id": rows["observation_id"].to_numpy(),
                          "interval_start_utc": rows["interval_start_utc"].to_numpy(),
                          "timestamp_original": rows.get("timestamp_original", pd.Series(pd.NA, index=rows.index)).to_numpy(),
                          "source_file": rows.get("source_file", pd.Series(pd.NA, index=rows.index)).to_numpy(),
                          "source_row": rows.get("source_row", pd.Series(pd.NA, index=rows.index)).to_numpy(),
                          "evidence": evidence if isinstance(evidence, str) else np.asarray(evidence, dtype=object),
                          "rule_id": rule_id})
        f["interval_start_utc"] = pd.Series(rows["interval_start_utc"].to_numpy(), dtype="datetime64[ns, UTC]")
        flags.append(f)

    src = df[df["source_row"].notna()]
    add(src[src["value_state"] == S.SOURCE_NULL], "SOURCE_NULL", "R-NULL", "blank or non-numeric raw value")
    inv = src[src["value_state"] == S.INVALID_PHYSICAL]
    lim = cfg["physical_limits"]
    add(inv, "INVALID_VALUE", "R-LIMITS",
        [f"standardized {v:g} outside project screening bounds [{lim[s]['min']:g}, {lim[s]['max']:g}]"
         for v, s in zip(inv["standardized_value"], inv["signal"])])
    amb = src[src["time_status"] == T.TIME_AMBIGUOUS]
    add(amb, "AMBIGUOUS_LOCAL_TIMESTAMP", "T-AMBIG", amb["time_note"])
    non = src[src["time_status"] == T.TIME_NONEXISTENT]
    add(non, "NONEXISTENT_LOCAL_TIMESTAMP", "T-NONEXIST", non["time_note"])
    unp = src[src["time_status"] == T.TIME_UNPARSEABLE]
    add(unp, "UNPARSEABLE_TIMESTAMP", "T-PARSE", "timestamp text does not match " + T.ORIGINAL_FORMAT)

    dup = src[src["precedence_role"].isin(["superseded_identical", "superseded_conflict", "conflict_unresolved"])]
    if len(dup):
        same_file = dup["source_id"] == dup["_win_source"]
        conflict = dup["precedence_role"] != "superseded_identical"
        ev = ("same sensor and interval as " + dup["_win_obs"].astype(str) + "; role " + dup["precedence_role"])
        add(dup[same_file & ~conflict], "DUPLICATE_TIMESTAMP_IDENTICAL", "P-DUP", ev[same_file & ~conflict])
        add(dup[same_file & conflict], "DUPLICATE_TIMESTAMP_CONFLICT", "P-DUP", ev[same_file & conflict])
        add(dup[~same_file & ~conflict], "OVERLAP_IDENTICAL_ACROSS_FILES", "P-RANK", ev[~same_file & ~conflict])
        add(dup[~same_file & conflict], "CONFLICTING_OVERLAPPING_SOURCE", "P-RANK", ev[~same_file & conflict])

    ceiling = src[(src["signal"] == "poa_irradiance") & (src["standardized_value"] == rules["poa_logger_ceiling_value"])]
    add(ceiling, "SUSPECTED_LOGGER_ARTEFACT", "A-CEILING",
        f"POA exactly {rules['poa_logger_ceiling_value']} W/m2: repeated maximum in all POA files (I08); value retained")

    for _, sg in segments.iterrows():
        if not sg.sampling_segment_id.endswith("_seg1"):
            first = src[src["sampling_segment_id"] == sg.sampling_segment_id].head(1)
            add(first, "SAMPLING_REGIME_CHANGE", "S-SEG",
                f"modal step changes to {sg.step_minutes:g} min from {sg.first_month}")

    add(ei[ei["coverage_status"] == "MISSING"].assign(observation_id=lambda x: x["sensor_id"] + ":missing:" +
        x["interval_start_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        "MISSING_EXPECTED_INTERVAL", "E-GRID", "no source row for this expected physical interval")
    add(ei[ei["coverage_status"] == "DST_AMBIGUOUS"].assign(observation_id=pd.NA),
        "UNRESOLVED_DST_INTERVAL", "T-AMBIG",
        "fall-back interval possibly covered by an ambiguous source row; not counted as missing or covered")
    add(ei[ei["coverage_status"] == "CONFLICT_UNRESOLVED"].assign(observation_id=pd.NA),
        "UNRESOLVED_CONFLICT_INTERVAL", "P-DUP", "conflicting rows of equal precedence; no value chosen")

    res = df[df["in_scope"] & df["observation_state"].isin(S.RESOLVED_STATES)].sort_values(
        ["sensor_id", "interval_start_utc"])
    off = res[~res["_on_grid"].astype(bool)]
    add(off, "OFF_GRID_TIMESTAMP", "E-GRID", "interval start not on the expected grid")
    gap = res.groupby("sensor_id")["interval_start_utc"].diff().dt.total_seconds() / 60
    irregular = res[gap.notna() & (gap != res["interval_minutes"])]
    add(irregular, "IRREGULAR_INTERVAL", "E-STEP",
        [f"gap from previous resolved row {g:g} min vs nominal {n:g}"
         for g, n in zip(gap[irregular.index], irregular["interval_minutes"])])

    # Unresolved daylight zeros. Geometry criterion for every sensor; for AC power also a
    # lookup of any resolved POA reading > threshold inside the same meter interval
    # (POA labels :00/:05/:10 fall inside meter [:00, :15)). No averaging.
    zeros = df[df["in_scope"] & (df["observation_state"] == S.OBSERVED_ZERO)]
    df["_day_zero"] = False
    if len(zeros):
        mid = zeros["interval_start_utc"] + pd.to_timedelta(zeros["interval_minutes"] / 2, unit="min")
        elev = pd.Series(T.solar_elevation_deg(mid, site["latitude"], site["longitude"], site["elevation_m"]),
                         index=zeros.index)
        poa = df[df["in_scope"] & (df["signal"] == "poa_irradiance") &
                 df["observation_state"].isin([S.OBSERVED_VALID, S.OBSERVED_ZERO])]
        bright = set(poa.loc[poa["standardized_value"] > rules["daylight_zero_poa_min_wm2"],
                             "interval_start_utc"].dt.floor("15min"))
        poa_hit = (zeros["signal"] == "ac_power") & zeros["interval_start_utc"].isin(bright)
        geom_hit = elev > rules["daylight_min_solar_elevation_deg"]
        hit = geom_hit | poa_hit
        day_zero = zeros[hit]
        ev = ("observed zero; solar elevation " + elev[hit].round(1).astype(str) + " deg at interval midpoint"
              + np.where(poa_hit[hit], f"; POA > {rules['daylight_zero_poa_min_wm2']:g} W/m2 within interval", "")
              + "; cause unknown (not missing, not confirmed outage, not confirmed valid)")
        add(day_zero, "DAYLIGHT_ZERO_UNRESOLVED", "A-DAYZERO", ev)
        df.loc[day_zero.index, "_day_zero"] = True

    # Catch-up pattern: physically invalid high meter value immediately after contiguous daylight zeros.
    meter = res[res["signal"] == "ac_power"].reset_index()
    for i in meter.index[meter["observation_state"] == S.INVALID_PHYSICAL]:
        if meter.loc[i, "standardized_value"] <= lim["ac_power"]["max"]:
            continue
        j, zeros_before = i - 1, []
        while j >= 0 and meter.loc[j, "observation_state"] == S.OBSERVED_ZERO and \
                meter.loc[j + 1, "interval_start_utc"] - meter.loc[j, "interval_start_utc"] == \
                pd.Timedelta(minutes=meter.loc[j, "interval_minutes"]) and \
                df.loc[meter.loc[j, "index"], "_day_zero"]:
            zeros_before.append(j)
            j -= 1
        if zeros_before:
            spike = df.loc[[meter.loc[i, "index"]]]
            add(spike, "SUSPECTED_LOGGER_ARTEFACT", "A-CATCHUP",
                f"value {meter.loc[i, 'standardized_value']:g} kW after {len(zeros_before)} contiguous daylight zero "
                "interval(s): suspected buffered catch-up; raw value retained")
            zr = df.loc[meter.loc[zeros_before, "index"]]
            add(zr, "SUSPECTED_LOGGER_ARTEFACT", "A-CATCHUP",
                f"daylight zero immediately before catch-up value at {meter.loc[i, 'observation_id']}")

    out = pd.concat(flags, ignore_index=True)
    out.insert(0, "site_id", cfg["site"]["site_id"])
    out["config_version"] = cfg["config_version"]
    out = out.sort_values(["flag_type", "sensor_id", "interval_start_utc", "source_row", "observation_id"],
                          kind="mergesort", na_position="last").reset_index(drop=True)
    out.insert(0, "flag_id", [f"F{i + 1:07d}" for i in range(len(out))])
    return out


def build_tables(raw_sources, cfg, site, source_hashes):
    """Pure transformation: {source_id: raw text frame} -> observations, expected intervals, flags, segments."""
    parts, segs = [], []
    for src in cfg["sources"]:
        o, sg = source_observations(raw_sources[src["source_id"]], src, cfg, source_hashes[src["source_id"]])
        parts.append(o)
        segs.append(sg)
    df = pd.concat(parts, ignore_index=True)
    segments = pd.concat(segs, ignore_index=True)
    df, start_utc, end_utc = apply_scope(df, cfg)
    df = resolve_precedence(df, cfg["flag_rules"]["duplicate_value_tolerance"])
    df = assign_states(df)
    df["_on_grid"] = True
    ei = expected_intervals(df, segments, cfg, start_utc, end_utc)
    flags = build_flags(df, ei, segments, cfg, site)

    notes = flags[flags["observation_id"].notna()].groupby("observation_id")["flag_type"].agg(
        lambda s: ";".join(sorted(set(s))))
    obs = pd.concat([df, missing_rows(ei, cfg)], ignore_index=True)
    note = obs["observation_id"].map(notes).fillna("")
    tn = obs["time_note"].fillna("")
    obs["quality_notes"] = np.where((note != "") & (tn != ""), note + " | " + tn, note + tn)
    obs = obs.sort_values(["sensor_id", "interval_start_utc", "precedence_rank", "source_row", "observation_id"],
                          kind="mergesort", na_position="last").reset_index(drop=True)
    obs["source_row"] = obs["source_row"].astype("Int64")
    obs = obs[OBS_COLUMNS]
    segments = segments[["sampling_segment_id", "source_id", "step_minutes", "first_month", "last_month",
                         "first_timestamp_original", "last_timestamp_original", "rows"]]
    return obs, ei, flags, segments


def site_metadata(cfg, root=ROOT):
    meta_path = root / cfg["site"]["metadata_file"]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    manifest = json.loads((root / "docs" / "source_manifest.json").read_text(encoding="utf-8"))
    entry = next(f for f in manifest["files"] if f["original_filename"] == meta_path.name)
    mods = meta["Modules"].values()
    dc_rule = cfg["site"]["dc_capacity"]
    raw_dc = float(meta["System"][dc_rule["raw_value_key"]])
    mount = meta["Mount"]["Mount 0"]
    row = {
        "site_id": cfg["site"]["site_id"], "site_name": meta["System"]["public_name"],
        "latitude": float(meta["Site"]["latitude"]), "longitude": float(meta["Site"]["longitude"]),
        "elevation_m": float(meta["Site"]["elevation(m)"]),
        "local_timezone": cfg["site"]["local_timezone"], "source_timezone_code": meta["System"]["timezone_code"],
        "tilt_deg": float(mount["tilt"]), "azimuth_deg": float(mount["azimuth"]),
        "tracking": mount["tracking"], "mounting_type": cfg["site"]["mounting_type"],
        "dc_capacity_raw_value": raw_dc, "dc_capacity_raw_unit": dc_rule["raw_unit"],
        "dc_capacity_kw": raw_dc * dc_rule["multiplier"],
        "dc_capacity_conversion_rule": f"{dc_rule['standardized_unit']} = {dc_rule['raw_unit']} * {dc_rule['multiplier']:g}",
        "module_count": int(sum(m["quantity"] for m in mods)),
        "module_model": ";".join(sorted({f"{m['manufacturer']} {m['model']}" for m in mods})),
        "dc_from_module_inventory_kw": round(sum(m["quantity"] for m in mods) * 0.310, 3),
        "inverter_count": len(meta["Inverters"]),
        "source": cfg["site"]["source"], "metadata_file": cfg["site"]["metadata_file"],
        "metadata_sha256": sha256_file(meta_path),
        "metadata_version": f"S3 Last-Modified {entry['last_modified']}; ETag {entry['etag']}",
    }
    return pd.DataFrame([row])


def run(out_dir=None, results_dir=None, cfg_path=CONFIG_PATH, root=ROOT):
    cfg = load_config(cfg_path)
    out_dir = Path(out_dir or root / cfg["outputs"]["processed_dir"])
    results_dir = Path(results_dir or root / cfg["outputs"]["results_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    meta = site_metadata(cfg, root)
    site = meta.iloc[0].to_dict()
    hashes = {s["source_id"]: sha256_file(root / s["file"]) for s in cfg["sources"]}
    raw = {s["source_id"]: read_source(s, cfg, root) for s in cfg["sources"]}
    obs, ei, flags, segments = build_tables(raw, cfg, site, hashes)
    tables = {"site_metadata": meta, "observations": obs, "expected_intervals": ei,
              "structural_quality_flags": flags, "sampling_segments": segments}
    for name, t in tables.items():
        t.to_parquet(out_dir / f"{name}.parquet", index=False)
    manifest = {
        "config_version": cfg["config_version"],
        "config_sha256": sha256_file(cfg_path),
        "code_sha256": {f: sha256_file(root / f) for f in CODE_FILES},
        "input_sha256": {s["file"]: hashes[s["source_id"]] for s in cfg["sources"]},
        "input_rows": {s["source_id"]: len(raw[s["source_id"]]) for s in cfg["sources"]},
        "tables": {name: {"rows": len(t), "content_sha256": table_hash(t)} for name, t in tables.items()},
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__},
    }
    (results_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tables, manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir")
    ap.add_argument("--results-dir")
    a = ap.parse_args()
    _, man = run(a.out_dir, a.results_dir)
    print(json.dumps(man["tables"], indent=2))
