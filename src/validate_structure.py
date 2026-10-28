"""Phase 2 structural validation of the standardized data layer.

Checks lineage, states, duplicates, DST handling, reconciliation and
reproducibility, and writes results/phase2/structural_validation_summary.csv
plus supporting tables. Also runs two informational analyses:
  - interval-label check: clear-day POA timing vs a pvlib clear-sky model;
  - completeness of 5-min POA groups inside each 15-min meter interval.
No PR, energy or irradiation is calculated.

Exit codes: 0 = every required check PASS; 1 = a required check FAIL;
2 = no failure but a required check SKIPPED (INCOMPLETE). INFO checks never
affect the exit code. results/phase2/validation_manifest.json records the
status, hashes, timing and versions.

Usage:
    python src/validate_structure.py [--skip-rerun]
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402
import standardize as S  # noqa: E402
import timestamps as T  # noqa: E402

ROOT = ingest.ROOT
TZ = "America/Los_Angeles"


def load(processed):
    return {n: pd.read_parquet(processed / f"{n}.parquet") for n in
            ("site_metadata", "observations", "expected_intervals", "structural_quality_flags", "sampling_segments")}


RESULTS = ("PASS", "FAIL", "INFO", "SKIPPED")


def check(rows, cid, name, ok, measured, expected="", evidence=""):
    """Record one check. ok: bool (PASS/FAIL) or 'INFO' / 'SKIPPED'. INFO checks are not required."""
    result = ok if isinstance(ok, str) else ("PASS" if ok else "FAIL")
    assert result in RESULTS, result
    rows.append({"check_id": cid, "check": name, "required": result != "INFO", "result": result,
                 "measured": measured, "expected": expected, "evidence": evidence})


def overall_status(summary):
    """(status, exit code): FAIL/1 if any required check failed; INCOMPLETE/2 if a required
    check was skipped; PASS/0 only when every required check passed. INFO never counts."""
    req = summary[summary["required"]]
    if (req["result"] == "FAIL").any():
        return "FAIL", 1
    if (req["result"] == "SKIPPED").any():
        return "INCOMPLETE", 2
    return "PASS", 0


def finalize(rows, results, started, skip_rerun, root=ROOT):
    """Write the summary and validation manifest, then return the process exit code."""
    summary = pd.DataFrame(rows, columns=["check_id", "check", "required", "result", "measured", "expected",
                                          "evidence"])
    summary.to_csv(results / "structural_validation_summary.csv", index=False)
    status, code = overall_status(summary)
    finished = pd.Timestamp.now(tz="UTC")
    files = ["src/validate_structure.py", "src/ingest.py", "src/timestamps.py", "src/standardize.py",
             "src/audit_phase1.py", "config/data_structure.yaml"]
    run_man_path = results / "run_manifest.json"
    run_man = json.loads(run_man_path.read_text(encoding="utf-8")) if run_man_path.exists() else {}
    versions = {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__,
                "pvlib": pvlib.__version__}
    for pkg in ("pyarrow", "yaml"):
        try:
            versions[pkg] = __import__(pkg).__version__
        except ImportError:
            versions[pkg] = None
    manifest = {
        "overall_status": status, "exit_code": code, "skip_rerun": skip_rerun,
        "started_utc": started.isoformat(), "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "result_counts": {r: int((summary["result"] == r).sum()) for r in RESULTS},
        "required_checks": int(summary["required"].sum()),
        "required_failed": summary.loc[summary["required"] & (summary["result"] == "FAIL"), "check_id"].tolist(),
        "required_skipped": summary.loc[summary["required"] & (summary["result"] == "SKIPPED"), "check_id"].tolist(),
        "file_sha256": {f: ingest.sha256_file(root / f) for f in files if (root / f).exists()},
        "pipeline_run_manifest": {"config_sha256": run_man.get("config_sha256"),
                                  "code_sha256": run_man.get("code_sha256"),
                                  "tables": run_man.get("tables")},
        "versions": versions,
    }
    (results / "validation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return code


def dst_dates(years):
    out = []
    for y in years:
        out.append(("spring", (pd.Timestamp(f"{y}-03-07") + pd.offsets.Week(weekday=6)).date()))
        out.append(("fall", (pd.Timestamp(f"{y}-10-31") + pd.offsets.Week(weekday=6)).date()))
    return out


def phase1_daylight_zero_events():
    """Recompute the Phase 1 sunny-zero events from raw data with the Phase 1 audit code."""
    import audit_phase1 as A
    with tempfile.TemporaryDirectory() as tmp:
        saved_out, A.OUT = A.OUT, Path(tmp)
        try:
            frames = {n: A.load(n)[0] for n in A.FILES}
            A.daylight_zero_events(frames)
            return pd.read_csv(Path(tmp) / "daylight_zero_events.csv", parse_dates=["start"])
        finally:
            A.OUT = saved_out


def compare_saved_phase1_events(ev):
    """Compare the saved Phase 1 events CSV with a recomputation, tolerating a re-saved date format."""
    path = ROOT / "results" / "phase1" / "daylight_zero_events.csv"
    if not path.exists():
        return False, "saved file missing"
    saved = pd.read_csv(path, dtype=str)
    for fmt, label in (("%Y-%m-%d %H:%M:%S", "original ISO format"), ("%d-%m-%Y %H:%M", "re-saved DD-MM-YYYY HH:MM format")):
        start = pd.to_datetime(saved["start"], format=fmt, errors="coerce")
        if start.notna().all():
            same = (len(saved) == len(ev) and (start.to_numpy() == ev["start"].to_numpy()).all()
                    and (saved["intervals"].astype(int).to_numpy() == ev["intervals"].to_numpy()).all())
            return same, f"{len(saved)} saved events, {label}; {'identical' if same else 'DIFFERENT'} events"
    return False, "saved start column matches no known date format"


def interval_label_check(obs, site, out_csv):
    """Best lag (minutes) between measured 5-min POA and a clear-sky POA model on clear days.

    With interval-start labels and a true 180 deg azimuth, the best lag should be near 0;
    with interval-end labels it should be near -5. Azimuth error also shifts the lag.
    """
    poa = obs[(obs["sensor_id"] == "poa_149574") & obs["observation_state"].isin([S.OBSERVED_VALID, S.OBSERVED_ZERO])]
    poa = poa.set_index("interval_start_utc")["standardized_value"].sort_index()
    start, end = poa.index.min().floor("D"), poa.index.max().ceil("D")
    minutes = pd.date_range(start - pd.Timedelta("1h"), end + pd.Timedelta("1h"), freq="1min")
    loc = pvlib.location.Location(site["latitude"], site["longitude"], tz="UTC", altitude=site["elevation_m"])
    sp = loc.get_solarposition(minutes)
    cs = loc.get_clearsky(minutes, solar_position=sp)
    model = pvlib.irradiance.get_total_irradiance(site["tilt_deg"], site["azimuth_deg"], sp["apparent_zenith"],
                                                  sp["azimuth"], cs["dni"], cs["ghi"], cs["dhi"])["poa_global"]
    model = model.fillna(0).clip(lower=0).to_numpy()
    csum = np.concatenate([[0.0], np.cumsum(model)])
    base = ((poa.index - minutes[0]) / pd.Timedelta("1min")).to_numpy().astype(int)
    local_day = poa.index.tz_convert(TZ).date
    lags = np.arange(-15, 16)
    rows = []
    frame = pd.DataFrame({"day": local_day, "meas": poa.to_numpy(), "base": base})
    for day, g in frame.groupby("day"):
        mod0 = (csum[g.base + 5] - csum[g.base]) / 5
        use = mod0 > 50
        if use.sum() < 60:
            continue
        corrs = []
        for k in lags:
            mk = (csum[g.base + k + 5] - csum[g.base + k]) / 5
            corrs.append(np.corrcoef(g.meas.to_numpy()[use], mk[use])[0, 1])
        corrs = np.array(corrs)
        best = int(lags[np.nanargmax(corrs)])
        ratio = g.meas.to_numpy()[use].sum() / mod0[use].sum()
        rows.append({"local_date": str(day), "n_intervals": int(use.sum()), "best_lag_min": best,
                     "corr_at_best": round(float(np.nanmax(corrs)), 5), "corr_at_0": round(float(corrs[15]), 5),
                     "measured_to_model_ratio": round(float(ratio), 3)})
    res = pd.DataFrame(rows)
    res.to_csv(out_csv, index=False)
    clear = res[(res["corr_at_best"] >= 0.998) & res["measured_to_model_ratio"].between(0.85, 1.15)].copy()
    clear["month"] = pd.to_datetime(clear["local_date"]).dt.month
    season = np.where(clear["month"].isin([11, 12, 1, 2]), "Nov-Feb", np.where(clear["month"].isin([5, 6, 7, 8]),
                                                                               "May-Aug", "other"))
    summary = clear.groupby(season)["best_lag_min"].describe()[["count", "25%", "50%", "75%"]]
    return clear, summary


def poa_group_completeness(ei, site, out_csv):
    """For each in-scope 15-min meter interval, how many of its three 5-min POA intervals are usable."""
    meter = ei[ei["sensor_id"] == "meter_149578"][["interval_start_utc"]].copy()
    poa = ei[ei["sensor_id"] == "poa_149574"].copy()
    poa["meter_start"] = poa["interval_start_utc"].dt.floor("15min")
    poa["usable"] = poa["resolved_observation_state"].isin([S.OBSERVED_VALID, S.OBSERVED_ZERO])
    g = poa.groupby("meter_start").agg(n_poa_expected=("usable", "size"), n_poa_usable=("usable", "sum"))
    m = meter.merge(g, left_on="interval_start_utc", right_index=True, how="left").fillna(0)
    mid = m["interval_start_utc"] + pd.Timedelta("7min30s")
    m["daylight"] = T.solar_elevation_deg(mid, site["latitude"], site["longitude"], site["elevation_m"]) > 10
    m["year"] = m["interval_start_utc"].dt.tz_convert(TZ).dt.year
    out = (m.groupby(["year", "daylight", "n_poa_usable"]).size().rename("meter_intervals").reset_index())
    out.to_csv(out_csv, index=False)
    return m


def main(skip_rerun=False):
    cfg = ingest.load_config()
    processed = ROOT / cfg["outputs"]["processed_dir"]
    results = ROOT / cfg["outputs"]["results_dir"]
    t = load(processed)
    obs, ei, flags, segs, meta = (t["observations"], t["expected_intervals"], t["structural_quality_flags"],
                                  t["sampling_segments"], t["site_metadata"])
    site = meta.iloc[0].to_dict()
    manifest = json.loads((ROOT / "docs" / "source_manifest.json").read_text(encoding="utf-8"))
    run_man = json.loads((results / "run_manifest.json").read_text(encoding="utf-8"))
    rows = []

    # V01 raw files unchanged
    bad = [f["original_filename"] for f in manifest["files"]
           if ingest.sha256_file(ROOT / f["local_path"]) != f["sha256"]]
    check(rows, "V01", "Raw files match Phase 1 SHA-256 hashes", not bad,
          f"{len(manifest['files']) - len(bad)}/{len(manifest['files'])} match", "all match", ";".join(bad))
    by_path = {f["local_path"]: f["sha256"] for f in manifest["files"]}
    hash_ok = all(run_man["input_sha256"][s["file"]] == by_path[s["file"]] for s in cfg["sources"])
    check(rows, "V01b", "Pipeline input hashes equal manifest hashes", hash_ok, "6 sources compared", "equal")

    # V02 lineage completeness and source row coverage
    src = obs[obs["source_row"].notna()]
    gen = obs[obs["source_row"].isna()]
    lineage_ok = src[["source_file", "source_hash", "source_id", "timestamp_original"]].notna().all().all()
    check(rows, "V02", "Every source-derived row has file, row, hash and original timestamp", bool(lineage_ok),
          f"{len(src)} source rows")
    per_src = []
    for s in cfg["sources"]:
        n_raw = run_man["input_rows"][s["source_id"]]
        r = src[src["source_id"] == s["source_id"]]["source_row"]
        per_src.append(len(r) == n_raw and r.min() == 2 and r.max() == n_raw + 1 and not r.duplicated().any()
                       and (src.loc[src["source_id"] == s["source_id"], "source_hash"] == by_path[s["file"]]).all())
    check(rows, "V02b", "Each source file's rows appear exactly once (rows 2..n+1) with the file hash", all(per_src),
          f"{sum(per_src)}/{len(per_src)} sources")
    check(rows, "V02c", "Generated rows are only EXPECTED_ROW_MISSING and carry no source lineage",
          bool((gen["observation_state"] == S.EXPECTED_ROW_MISSING).all() and gen["raw_value"].isna().all()
               and gen["source_file"].isna().all()), f"{len(gen)} generated rows")

    # V03 lineage spot check against raw text
    rng = np.random.default_rng(20261027)
    sample = src.iloc[rng.choice(len(src), size=5000, replace=False)]
    spike = src[(src["sensor_id"] == "meter_149578") & (src["raw_value"] == "1783.68")]
    sample = pd.concat([sample, spike])
    mismatch = 0
    for s in cfg["sources"]:
        part = sample[sample["source_id"] == s["source_id"]]
        if part.empty:
            continue
        raw = ingest.read_source(s, cfg).set_index("source_row")
        r = raw.loc[part["source_row"].astype(int).to_numpy()]
        mismatch += int((r["timestamp_original"].to_numpy() != part["timestamp_original"].to_numpy()).sum())
        mismatch += int((r["raw_text"].to_numpy() != part["raw_value"].to_numpy()).sum())
    check(rows, "V03", "Random 5,000 rows + spike row: timestamp text and raw value equal the raw file line",
          mismatch == 0, f"{len(sample)} rows, {mismatch} mismatches", "0 mismatches", "seed 20261027")

    # V04 missing vs null vs zero
    nulls = obs[obs["observation_state"] == S.SOURCE_NULL]
    check(rows, "V04", "Missing expected rows distinct from source nulls",
          bool(nulls["source_row"].notna().all() and gen["source_row"].isna().all()),
          f"missing={len(gen)}, source_null={len(nulls)}")
    zeros = obs[obs["observation_state"] == S.OBSERVED_ZERO]
    inscope_src = src[src["observation_state"] != S.EXCLUDED_BY_SCOPE]
    raw_is_zero = S.parse_raw_value(inscope_src["raw_value"]).to_numpy() == 0
    zero_states = inscope_src.loc[raw_is_zero, "observation_state"].value_counts().to_dict()
    allowed = {S.OBSERVED_ZERO, S.TIME_AMBIGUOUS, S.DUPLICATE_IDENTICAL, S.DUPLICATE_CONFLICT}
    zero_raw_nonzero = (S.parse_raw_value(zeros["raw_value"]).to_numpy() != 0).sum()
    check(rows, "V05", "Raw zeros (re-parsed from raw text, in scope) are OBSERVED_ZERO unless time-ambiguous or "
                       "duplicate; every OBSERVED_ZERO has raw 0 and a source row; zeros are never generated",
          set(zero_states) <= allowed and zero_raw_nonzero == 0 and bool(zeros["source_row"].notna().all()),
          f"in-scope raw zeros by state: {zero_states}")

    # V06 duplicates and overlaps reported
    sup = obs[obs["precedence_role"].isin(["superseded_identical", "superseded_conflict", "conflict_unresolved"])]
    flagged = set(flags.loc[flags["flag_type"].isin(["DUPLICATE_TIMESTAMP_IDENTICAL", "DUPLICATE_TIMESTAMP_CONFLICT",
                                                     "OVERLAP_IDENTICAL_ACROSS_FILES",
                                                     "CONFLICTING_OVERLAPPING_SOURCE"]), "observation_id"])
    check(rows, "V06", "Every superseded or conflicting duplicate row is flagged", set(sup["observation_id"]) <= flagged,
          f"{len(sup)} rows; roles {sup['precedence_role'].value_counts().to_dict()}")

    # V07 monotonic UTC within resolved streams
    mono = []
    for s in [s for s in cfg["sources"] if s["v1_role"] == "primary"]:
        r = obs[(obs["source_id"] == s["source_id"]) & obs["timestamp_utc"].notna()].sort_values("source_row")
        mono.append(r["timestamp_utc"].is_monotonic_increasing and not r["timestamp_utc"].duplicated().any())
    res = obs[obs["observation_state"].isin(S.RESOLVED_STATES)]
    dup_res = res.duplicated(["sensor_id", "interval_start_utc"]).sum()
    check(rows, "V07", "UTC strictly increasing in file order for each primary source; one resolved row per interval",
          all(mono) and dup_res == 0, f"monotonic={mono}, duplicate resolved intervals={dup_res}")

    # V08 DST policy
    years = range(2017, 2024)
    dst_rows = []
    for kind, d in dst_dates(years):
        for sensor, step in (("meter_149578", 15), ("poa_149574", 5)):
            day = ei[(ei["sensor_id"] == sensor) & (ei["local_reporting_date"] == str(d))]
            if day.empty:
                continue
            o = obs[(obs["sensor_id"] == sensor) & (obs["timestamp_original"].str.startswith(str(d), na=False))]
            dst_rows.append({
                "transition": kind, "local_date": str(d), "sensor_id": sensor,
                "day_length_h": day["local_day_length_h"].iloc[0], "expected_intervals": len(day),
                "expected_for_day_length": int(day["local_day_length_h"].iloc[0] * 60 / step),
                "covered": int((day["coverage_status"] == "COVERED").sum()),
                "missing": int((day["coverage_status"] == "MISSING").sum()),
                "dst_ambiguous": int((day["coverage_status"] == "DST_AMBIGUOUS").sum()),
                "source_rows_time_ambiguous": int((o["observation_state"] == S.TIME_AMBIGUOUS).sum()),
                "source_rows_in_local_02xx": int(o["timestamp_original"].str.slice(11, 13).eq("02").sum()),
            })
    dst = pd.DataFrame(dst_rows)
    dst.to_csv(results / "dst_transition_summary.csv", index=False)
    spring, fall = dst[dst["transition"] == "spring"], dst[dst["transition"] == "fall"]
    dst_ok = (bool((dst["expected_intervals"] == dst["expected_for_day_length"]).all())
              and bool((spring["day_length_h"] == 23).all()) and bool((fall["day_length_h"] == 25).all())
              and bool((spring["source_rows_in_local_02xx"] == 0).all())
              and bool((spring["source_rows_time_ambiguous"] == 0).all()))
    amb = obs[obs["observation_state"] == S.TIME_AMBIGUOUS]
    amb_hours_ok = bool(amb["timestamp_original"].str.slice(11, 13).eq("01").all())
    nonexist = int((obs["time_status"] == T.TIME_NONEXISTENT).sum())
    check(rows, "V08", "DST: 23 h spring / 25 h fall days, expected intervals match day length, "
                       "no spring 02:xx rows or generated misses, ambiguous rows only in fall 01:xx",
          dst_ok and amb_hours_ok and nonexist == 0,
          f"{len(spring)} spring and {len(fall)} fall sensor-days; ambiguous rows={len(amb)}; nonexistent={nonexist}",
          evidence="results/phase2/dst_transition_summary.csv")

    # V09 reproducibility
    if skip_rerun:
        check(rows, "V09", "Re-running the pipeline reproduces identical table contents", "SKIPPED",
              "not run (--skip-rerun); validation is INCOMPLETE")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            _, man2 = ingest.run(out_dir=Path(tmp) / "p", results_dir=Path(tmp) / "r")
            same = {n: man2["tables"][n]["content_sha256"] == run_man["tables"][n]["content_sha256"]
                    for n in run_man["tables"]}
            bytes_same = {n: ingest.sha256_file(Path(tmp) / "p" / f"{n}.parquet")
                          == ingest.sha256_file(processed / f"{n}.parquet") for n in run_man["tables"]}
        check(rows, "V09", "Re-running the pipeline reproduces identical table contents", all(same.values()),
              f"content equal: {sum(same.values())}/{len(same)}; parquet bytes equal: {sum(bytes_same.values())}/{len(bytes_same)}")

    # V10 reconciliation
    n_src = sum(run_man["input_rows"].values())
    recon = [{"item": "source rows (6 files)", "rows": n_src},
             {"item": "generated EXPECTED_ROW_MISSING rows", "rows": len(gen)},
             {"item": "observations table rows", "rows": len(obs)}]
    for st, n in obs["observation_state"].value_counts().sort_index().items():
        recon.append({"item": f"state {st}", "rows": int(n)})
    for cs, n in ei.groupby(["sensor_id", "coverage_status"]).size().items():
        recon.append({"item": f"expected {cs[0]} {cs[1]}", "rows": int(n)})
    pd.DataFrame(recon).to_csv(results / "row_reconciliation.csv", index=False)
    ok_total = len(obs) == n_src + len(gen)
    ok_missing = len(gen) == int((ei["coverage_status"] == "MISSING").sum())
    covered = ei[ei["coverage_status"] == "COVERED"]
    inscope_res = obs[obs["observation_state"].isin(S.RESOLVED_STATES)]
    ok_cov = len(covered) == len(inscope_res) and set(covered["resolved_observation_id"]) == set(inscope_res["observation_id"])
    excl = (obs["observation_state"] == S.EXCLUDED_BY_SCOPE).sum()
    inscope_src = src[src["observation_state"] != S.EXCLUDED_BY_SCOPE]
    check(rows, "V10", "Rows reconcile: observations = source rows + generated missing; expected = covered + missing "
                       "+ DST-ambiguous + conflict; every resolved row covers exactly one expected interval",
          ok_total and ok_missing and ok_cov,
          f"{len(obs)} = {n_src} + {len(gen)}; source rows = {len(inscope_src)} in scope + {excl} excluded; "
          f"covered {len(covered)} = resolved {len(inscope_res)}", evidence="results/phase2/row_reconciliation.csv")

    # V11 units
    num = S.parse_raw_value(src["raw_value"]).to_numpy()
    mult = src["sensor_id"].map(lambda s: cfg["sensors"][s]["multiplier"]).to_numpy()
    off = src["sensor_id"].map(lambda s: cfg["sensors"][s]["offset"]).to_numpy()
    diff = np.nanmax(np.abs(src["standardized_value"].to_numpy() - (num * mult + off)))
    units_ok = set(obs.loc[obs["sensor_id"] == "meter_149578", "standardized_unit"]) == {"kW"} and \
        set(obs.loc[obs["sensor_id"] == "poa_149574", "standardized_unit"]) == {"W/m^2"} and \
        meta["dc_capacity_kw"].iloc[0] == 893.0
    check(rows, "V11", "Standardized = raw x multiplier + offset; units kW, W/m^2; DC 893.0 kW",
          units_ok and diff == 0, f"max abs difference {diff:g}")

    # V12 spike, V13 ceiling, V14 Phase 1 daylight-zero events
    sp = spike.iloc[0]
    check(rows, "V12", "1,783.68 kW spike traceable and INVALID_PHYSICAL with documented rule",
          sp["observation_state"] == S.INVALID_PHYSICAL and "SUSPECTED_LOGGER_ARTEFACT" in sp["quality_notes"],
          f"{sp['observation_id']} raw {sp['raw_value']} at {sp['timestamp_original']}; state {sp['observation_state']}",
          evidence=sp["quality_notes"])
    ceil = obs[(obs["sensor_id"] == "poa_149574") & (obs["standardized_value"] == 1400.0)]
    ceil_in = ceil[ceil["observation_state"] != S.EXCLUDED_BY_SCOPE]
    check(rows, "V13", "POA 1400 W/m2 values retained and flagged, not removed",
          bool((ceil_in["observation_state"] == S.OBSERVED_VALID).all()
               and ceil["quality_notes"].str.contains("SUSPECTED_LOGGER_ARTEFACT").all()),
          f"{len(ceil)} rows ({len(ceil_in)} in scope), all flagged")
    ev = phase1_daylight_zero_events()
    saved_match, saved_note = compare_saved_phase1_events(ev)
    meter_obs = obs[obs["sensor_id"] == "meter_149578"]
    status = []
    for _, r in ev.iterrows():
        src_id = "meter_main" if r.file == "meter_2017_2023" else "meter_2024"
        ts = {str(r.start + pd.Timedelta(minutes=15 * k)) for k in range(int(r.intervals))}
        o = meter_obs[(meter_obs["source_id"] == src_id) & meter_obs["timestamp_original"].isin(ts)]
        if src_id == "meter_main":
            status.append(len(o) == r.intervals and (o["observation_state"] == S.OBSERVED_ZERO).all()
                          and o["quality_notes"].str.contains("DAYLIGHT_ZERO_UNRESOLVED").all())
        else:
            status.append(len(o) == r.intervals and (o["observation_state"] == S.EXCLUDED_BY_SCOPE).all()
                          and (o["value_state"] == S.OBSERVED_ZERO).all())
    n_main = int((ev.file == "meter_2017_2023").sum())
    check(rows, "V14", "Phase 1 sunny zero-output runs: in-scope runs are OBSERVED_ZERO + DAYLIGHT_ZERO_UNRESOLVED; "
                       "runs in the 2024 increment are EXCLUDED_BY_SCOPE with value_state OBSERVED_ZERO",
          all(status), f"{sum(status)}/{len(status)} runs ({n_main} in scope, {len(ev) - n_main} in 2024 increment)",
          evidence="events recomputed from raw data with src/audit_phase1.py (not read from the saved CSV)")
    check(rows, "V14b", "Saved results/phase1/daylight_zero_events.csv matches the recomputed Phase 1 events (INFO)",
          "INFO", saved_note, "same 43 events", "saved file integrity only; V14 does not depend on it")

    # V15 sampling segments
    check(rows, "V15", "Sampling regimes identified per source", "INFO",
          "; ".join(f"{r.sampling_segment_id}: {r.step_minutes:g} min {r.first_month}..{r.last_month}"
                    for r in segs.itertuples()))

    # V16 informational analyses
    clear, summary = interval_label_check(obs, site, results / "interval_label_check.csv")
    check(rows, "V16", "Interval-label check: best lag of measured POA vs clear-sky model on clear days (INFO)",
          "INFO", "; ".join(f"{k}: n={int(v['count'])}, median {v['50%']:g} min, IQR {v['25%']:g}..{v['75%']:g}"
                            for k, v in summary.iterrows()),
          "observed discrepancy under the tested model (pvlib Ineichen clear sky, 25 deg tilt, metadata azimuth); "
          "not a proof of label convention, clock offset or true azimuth", "results/phase2/interval_label_check.csv")
    sens = []
    for az in (190.0, 200.0):
        with tempfile.TemporaryDirectory() as tmp:
            _, summ = interval_label_check(obs, dict(site, azimuth_deg=az), Path(tmp) / "x.csv")
        for k, v in summ.iterrows():
            sens.append({"azimuth_deg": az, "season": k, "clear_days": int(v["count"]),
                         "median_best_lag_min": v["50%"], "iqr_min": f"{v['25%']:g}..{v['75%']:g}"})
    for k, v in summary.iterrows():
        sens.append({"azimuth_deg": site["azimuth_deg"], "season": k, "clear_days": int(v["count"]),
                     "median_best_lag_min": v["50%"], "iqr_min": f"{v['25%']:g}..{v['75%']:g}"})
    sens = pd.DataFrame(sens).sort_values(["azimuth_deg", "season"])
    sens.to_csv(results / "azimuth_sensitivity_check.csv", index=False)
    check(rows, "V16b", "Azimuth sensitivity of the clear-day lag (INFO)", "INFO",
          "; ".join(f"az {r.azimuth_deg:g} {r.season}: {r.median_best_lag_min:g} min" for r in sens.itertuples()),
          "if one azimuth gives ~0 in all seasons, azimuth error is a live explanation",
          "results/phase2/azimuth_sensitivity_check.csv")
    m = poa_group_completeness(ei, site, results / "poa_15min_group_completeness.csv")
    day = m[m["daylight"]]
    check(rows, "V17", "15-min meter intervals with 3/2/1/0 usable 5-min POA intervals (daylight, INFO)", "INFO",
          "; ".join(f"{int(k)} usable: {v}" for k, v in day["n_poa_usable"].value_counts().sort_index(ascending=False).items()),
          evidence="results/phase2/poa_15min_group_completeness.csv")
    aligned = bool((m["n_poa_expected"] == 3).all())
    check(rows, "V18", "Every 15-min meter interval contains exactly three expected 5-min POA intervals", aligned,
          f"{int((m['n_poa_expected'] == 3).sum())}/{len(m)}")

    # Supporting count tables
    pd.crosstab(obs["source_id"].fillna("generated"), obs["observation_state"]).to_csv(
        results / "observation_state_counts.csv")
    flags.groupby(["flag_type", "sensor_id", "rule_id"]).size().rename("flags").reset_index().to_csv(
        results / "flag_counts.csv", index=False)
    return rows


def cli(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-rerun", action="store_true",
                    help="skip the reproducibility re-run (overall status becomes INCOMPLETE, exit code 2)")
    args = ap.parse_args(argv)
    results = ROOT / ingest.load_config()["outputs"]["results_dir"]
    started = pd.Timestamp.now(tz="UTC")
    rows = main(args.skip_rerun)
    code = finalize(rows, results, started, args.skip_rerun)
    summary = pd.DataFrame(rows)
    print(summary[["check_id", "required", "result", "measured"]].to_string(index=False, max_colwidth=100))
    print(f"overall: {overall_status(summary)[0]} (exit code {code})")
    raise SystemExit(code)


if __name__ == "__main__":
    cli()
