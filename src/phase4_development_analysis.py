"""Phase 4 development analysis and structural window sampling.

DEVELOPMENT DATA ONLY (2018-02-01 to 2020-01-31) for every value-based measurement:
  - zero-output rate by POA level and the zero-output policy comparison;
  - POA stale-run profile (tolerance x minimum length);
  - POA vs clear-sky timing lag per day (baseline and alert burden);
  - development status comparison: phase3-v1 vs basic vs improved (experiment-v1 prototype rules).

EVALUATION PERIOD: structural eligibility only (Phase 2 coverage status and observation
state inside the solar-geometry window). No evaluation values, PR or detector outputs are
read or computed. The eligible window sample is drawn with a fixed seed and frozen.

The check logic here is a development prototype; Phase 5 implements and tests the detectors.

Usage:
    python src/phase4_development_analysis.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402
import metrics as M  # noqa: E402
import pair_intervals as P  # noqa: E402

ROOT = ingest.ROOT
OUT = ROOT / "results" / "phase4"
PROC = ROOT / "data" / "processed"
EXP = yaml.safe_load((ROOT / "config" / "experiment.yaml").read_text(encoding="utf-8"))
MCFG = yaml.safe_load((ROOT / "config" / "metric_definition.yaml").read_text(encoding="utf-8"))
USABLE = {"OBSERVED_VALID", "OBSERVED_ZERO"}


def site():
    return pd.read_parquet(PROC / "site_metadata.parquet").iloc[0].to_dict()


# ---------- clear-sky timing (prototype of improved check I3) ----------
def clearsky_minutes(s, t0, t1):
    mins = pd.date_range(t0, t1, freq="1min")
    loc = pvlib.location.Location(s["latitude"], s["longitude"], tz="UTC", altitude=s["elevation_m"])
    sp = loc.get_solarposition(mins)
    cs = loc.get_clearsky(mins, model="ineichen", solar_position=sp)
    mod = pvlib.irradiance.get_total_irradiance(s["tilt_deg"], s["azimuth_deg"], sp["apparent_zenith"], sp["azimuth"],
                                                cs["dni"], cs["ghi"], cs["dhi"])["poa_global"]
    return mins, np.concatenate([[0.0], np.cumsum(mod.fillna(0).clip(lower=0).to_numpy())]), \
        sp["apparent_elevation"].to_numpy()


def timing_lags(poa, s, tcfg):
    """Per local day: best lag (min) of measured 5-min POA vs clear-sky POA, plus evidence."""
    poa = poa[poa["state"].isin(USABLE)]
    t0 = poa["interval_start_utc"].min().floor("D") - pd.Timedelta("3h")
    t1 = poa["interval_start_utc"].max().ceil("D") + pd.Timedelta("3h")
    mins, csum, elev = clearsky_minutes(s, t0, t1)
    base = ((poa["interval_start_utc"] - mins[0]) / pd.Timedelta("1min")).to_numpy().astype(int)
    lags = np.arange(-tcfg["search_range_min"], tcfg["search_range_min"] + 1)
    rows = []
    for day, idx in pd.Series(np.arange(len(poa)), index=poa["local_reporting_date"].to_numpy()).groupby(level=0):
        b, meas = base[idx.to_numpy()], poa["value"].to_numpy()[idx.to_numpy()]
        mod0 = (csum[b + 5] - csum[b]) / 5
        use = (elev[b + 2] > MCFG["reporting_window"]["min_solar_elevation_deg"]) & \
              (mod0 > tcfg["min_model_wm2"])
        row = {"local_reporting_date": day, "n_intervals": int(use.sum()), "best_lag_min": np.nan,
               "corr_at_best": np.nan, "energy_ratio": np.nan}
        if use.sum() >= tcfg["min_intervals"]:
            cor = np.array([np.corrcoef(meas[use], ((csum[b + k + 5] - csum[b + k]) / 5)[use])[0, 1] for k in lags])
            i = int(np.nanargmax(cor))
            row.update(best_lag_min=int(lags[i]), corr_at_best=float(cor[i]),
                       energy_ratio=float(meas[use].sum() / mod0[use].sum()))
        rows.append(row)
    r = pd.DataFrame(rows)
    r["conclusive"] = (r["n_intervals"] >= tcfg["min_intervals"]) & (r["corr_at_best"] >= tcfg["min_corr"]) & \
        r["energy_ratio"].between(*tcfg["energy_ratio_range"])
    return r


# ---------- stale runs (prototype of improved check I2) ----------
def stale_runs(poa, window_starts, tol, min_values):
    """Longest run of consecutive usable in-window POA values with |delta| <= tol, per day."""
    p = poa[poa["interval_start_utc"].dt.floor("15min").isin(window_starts)].sort_values("interval_start_utc")
    p = p.reset_index(drop=True)
    usable = p["state"].isin(USABLE)
    contig = p["interval_start_utc"].diff() == pd.Timedelta("5min")
    same = (p["value"].diff().abs() <= tol + 1e-9) & contig & usable & usable.shift(fill_value=False)
    rid = (~same).cumsum()
    run_len = same.groupby(rid).transform("sum") + 1
    p["run_values"] = np.where(same | same.shift(-1, fill_value=False), run_len, 1)
    return p.groupby("local_reporting_date")["run_values"].max()


# ---------- zero-output policy (basic check B5) ----------
def zero_features(pairs, zcfg):
    w = pairs[pairs["in_window"] & pairs["paired"]].copy()
    w["poa_mean"] = w[["poa0_wm2_value", "poa1_wm2_value", "poa2_wm2_value"]].mean(axis=1)
    day_h = w.groupby("local_reporting_date")["h_poa_kwh_m2"].sum()
    z = w[w["meter_state"] == "OBSERVED_ZERO"]
    share = (z.groupby("local_reporting_date")["h_poa_kwh_m2"].sum() / day_h).reindex(day_h.index).fillna(0.0)
    susp = z[z["poa_mean"] >= zcfg["suspicious_meter_zero_min_poa_wm2"]].groupby("local_reporting_date").size()
    poa0 = w[(w[["poa0_wm2_value", "poa1_wm2_value", "poa2_wm2_value"]] == 0).any(axis=1)
             & (w["clearsky_kwh_m2"] / w["interval_hours"] * 1000 >= zcfg["suspicious_poa_zero_min_clearsky_wm2"])]
    return pd.DataFrame({"zero_share_of_h": share,
                         "n_suspicious_meter_zero": susp.reindex(day_h.index).fillna(0).astype(int),
                         "n_suspicious_poa_zero": poa0.groupby("local_reporting_date").size()
                         .reindex(day_h.index).fillna(0).astype(int)})


def method_status(daily, zf, stale, timing, exp, method):
    """Prototype day status for 'basic' or 'improved' from shared outputs plus method checks."""
    b, imp, z = exp["methods"]["basic"], exp["methods"]["improved"], exp["methods"]["basic"]["zero_output_policy"]
    rows = []
    for _, d in daily.iterrows():
        day = d["local_reporting_date"]
        ins, rev, ctx = [], set(), []
        codes = [c for c in str(d["reason_codes"]).split(";") if c and c != "nan"]
        for c in codes:                                       # shared prerequisites carried from the daily table
            if c in ("NO_WINDOW_INTERVALS", "NO_PAIRED_DATA", "INVALID_CAPACITY", "PR_UNAVAILABLE", "LOW_IRRADIATION"):
                ins.append(c)
            if c in ("INVALID_INPUT_IN_WINDOW", "SUSPECTED_CATCH_UP", "UNRESOLVED_TIME_IN_WINDOW",
                     "NEGATIVE_INPUT_IN_WINDOW"):
                rev.add(c)
        if d["paired_intervals"] > 0 and d["time_coverage"] < b["min_time_coverage"]:
            ins.append("LOW_TIME_COVERAGE")
        f = zf.loc[day] if day in zf.index else None
        if f is not None:
            if f["n_suspicious_meter_zero"] > 0:
                rev.add("SUSPICIOUS_ZERO_OUTPUT")
            if f["zero_share_of_h"] >= z["material_zero_share_of_h"]:
                rev.add("MATERIAL_ZERO_OUTPUT")
            if f["n_suspicious_poa_zero"] > 0:
                rev.add("SUSPICIOUS_POA_ZERO")
        if method == "improved":
            if d["paired_intervals"] > 0 and not (d["clearsky_weighted_coverage"] >=
                                                  imp["midday_completeness"]["min_clearsky_weighted_coverage"]):
                ins.append("LOW_CLEARSKY_WEIGHTED_COVERAGE")
            if stale.get(day, 1) >= imp["poa_stale"]["min_run_values"]:
                rev.add("POA_STALE")
            t = timing.loc[day] if day in timing.index else None
            if t is None or not t["conclusive"]:
                ctx.append("TIMING_INCONCLUSIVE")
            elif abs(t["best_lag_min"] - imp["timing"]["baseline_lag_min"]) >= imp["timing"]["threshold_min"]:
                rev.add("TIMING_INCONSISTENT")
        status = M.resolve_status(ins, rev)
        rows.append({"local_reporting_date": day, "method": method, "status": status,
                     "reason_codes": ";".join(ins + sorted(rev)), "context_codes": ";".join(ctx)})
    return pd.DataFrame(rows)


def development(exp):
    s = site()
    a, b = exp["periods"]["development"]["start_local_date"], exp["periods"]["development"]["end_local_date"]
    pairs = pd.read_parquet(ROOT / "results/phase3/paired_intervals_dev.parquet")
    daily = pd.read_csv(ROOT / "results/phase3/daily_pr_dev.csv")
    poa = P.load_stream(PROC, "poa_149574", a, b)
    w = pairs[pairs["in_window"] & pairs["paired"]].copy()
    w["poa_mean"] = w[["poa0_wm2_value", "poa1_wm2_value", "poa2_wm2_value"]].mean(axis=1)

    # zero-output rate by POA level (motivates the policy)
    bins = [-1, 10, 25, 50, 100, 150, 200, 300, 400, 600, 2000]
    zr = w.groupby(pd.cut(w["poa_mean"], bins), observed=False).agg(
        paired_intervals=("meter_state", "size"),
        zero_intervals=("meter_state", lambda x: int((x == "OBSERVED_ZERO").sum())),
        median_kw=("meter_kw_value", "median")).reset_index()
    zr["zero_rate"] = zr["zero_intervals"] / zr["paired_intervals"]
    zr["poa_mean"] = zr["poa_mean"].astype(str)
    zr.to_csv(OUT / "dev_zero_rate_by_poa.csv", index=False)

    zcfg = exp["methods"]["basic"]["zero_output_policy"]
    zf = zero_features(pairs, zcfg)
    window = set(pairs.loc[pairs["in_window"], "interval_start_utc"])
    icfg = exp["methods"]["improved"]
    grid = []
    for tol in (0.0, 0.05, 0.5, 1.0):
        for k in (3, 4, 6, 9, 12):
            mx = stale_runs(poa, window, tol, k)
            grid.append({"tolerance_wm2": tol, "min_run_values": k, "dev_days_flagged": int((mx >= k).sum())})
    pd.DataFrame(grid).to_csv(OUT / "dev_stale_run_grid.csv", index=False)
    stale = stale_runs(poa, window, icfg["poa_stale"]["tolerance_wm2"], icfg["poa_stale"]["min_run_values"])

    tim = timing_lags(poa, s, icfg["timing"]).set_index("local_reporting_date")
    tim.to_csv(OUT / "dev_timing_lags.csv")
    conc = tim[tim["conclusive"]]
    tim_summary = {"dev_days": len(tim), "conclusive_days": len(conc),
                   "baseline_median_lag_min": float(conc["best_lag_min"].median()),
                   "lag_iqr_min": [float(conc["best_lag_min"].quantile(0.25)),
                                   float(conc["best_lag_min"].quantile(0.75))],
                   "flagged_at_threshold": {str(t): int((abs(conc["best_lag_min"] - conc["best_lag_min"].median()) >= t)
                                                         .sum()) for t in (5, 6, 8, 10)},
                   "conclusive_days_by_month": conc.index.str.slice(5, 7).value_counts().sort_index().to_dict()}

    # status comparison
    basic = method_status(daily, zf, stale, tim, exp, "basic")
    improved = method_status(daily, zf, stale, tim, exp, "improved")
    rows = []
    for name, st in (("phase3-v1 original (Phase 3 run)", daily), ("basic experiment-v1", basic),
                     ("improved experiment-v1", improved)):
        counts = st["status"].value_counts()
        reasons = pd.Series([c for r in st["reason_codes"].fillna("") for c in str(r).split(";") if c]).value_counts()
        row = {"policy": name, "days": len(st),
               "CHECKS_PASSED": int(counts.get("CHECKS_PASSED", 0)),
               "REVIEW_REQUIRED": int(counts.get("REVIEW_REQUIRED", 0)),
               "INSUFFICIENT_DATA": int(counts.get("INSUFFICIENT_DATA", 0))}
        row["reporting_coverage"] = row["CHECKS_PASSED"] / row["days"]
        row["review_burden"] = row["REVIEW_REQUIRED"] / row["days"]
        for rc, n in reasons.items():
            row[f"reason:{rc}"] = int(n)
        rows.append(row)
    for m in (0.005, 0.01, 0.02):                     # sensitivity of the materiality threshold (not chosen by pass rate)
        mat = (zf["zero_share_of_h"] >= m) | (zf["n_suspicious_meter_zero"] > 0) | (zf["n_suspicious_poa_zero"] > 0)
        rows.append({"policy": f"zero policy only, materiality {m:.3f} (sensitivity)", "days": len(daily),
                     "zero_policy_review_days": int(mat.sum())})
    comp = pd.DataFrame(rows)
    comp.to_csv(OUT / "development_policy_comparison.csv", index=False)

    # PR effect of zeros left in passed days under the basic zero policy
    passed = basic[basic["status"] == "CHECKS_PASSED"]["local_reporting_date"]
    eff = []
    for day in passed:
        dp = w[w["local_reporting_date"] == day]
        zz = dp[dp["meter_state"] == "OBSERVED_ZERO"]
        if zz.empty:
            continue
        keep = dp.drop(zz.index)
        pr_all = M.performance_ratio(dp["e_ac_kwh"].sum(), 893.0, dp["h_poa_kwh_m2"].sum())
        pr_wo = M.performance_ratio(keep["e_ac_kwh"].sum(), 893.0, keep["h_poa_kwh_m2"].sum())
        eff.append({"local_reporting_date": day, "n_zero_intervals": len(zz),
                    "zero_share_of_h": zz["h_poa_kwh_m2"].sum() / dp["h_poa_kwh_m2"].sum(),
                    "pp_difference_vs_without_zeros": M.percentage_point_difference(pr_all, pr_wo)})
    eff = pd.DataFrame(eff)
    eff.to_csv(OUT / "dev_zero_pr_effect_passed_days.csv", index=False)
    pd.concat([basic, improved]).to_csv(OUT / "dev_method_status_prototype.csv", index=False)
    return comp, tim_summary, eff, zr


def evaluation_eligibility(exp):
    """Structural eligibility of evaluation days. Reads coverage status and state only."""
    ev = exp["periods"]["evaluation"]
    s = site()
    a = (pd.Timestamp(ev["start_local_date"]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    b = (pd.Timestamp(ev["end_local_date"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    ei = pd.read_parquet(PROC / "expected_intervals.parquet",
                         columns=["sensor_id", "interval_start_utc", "interval_end_utc", "local_reporting_date",
                                  "local_day_length_h", "coverage_status", "resolved_observation_state"])
    ei = ei[(ei["local_reporting_date"] >= a) & (ei["local_reporting_date"] < b)]
    meter = ei[ei["sensor_id"] == "meter_149578"].copy()
    mid = meter["interval_start_utc"] + pd.Timedelta("7min30s")
    from timestamps import solar_elevation_deg
    meter["in_window"] = solar_elevation_deg(mid, s["latitude"], s["longitude"], s["elevation_m"]) > \
        MCFG["reporting_window"]["min_solar_elevation_deg"]
    win = meter[meter["in_window"]]
    ok_m = (win["coverage_status"] == "COVERED") & win["resolved_observation_state"].isin(USABLE)
    poa = ei[ei["sensor_id"] == "poa_149574"].copy()
    poa["meter_start"] = poa["interval_start_utc"].dt.floor("15min")
    poa = poa[poa["meter_start"].isin(set(win["interval_start_utc"]))]
    ok_p = (poa["coverage_status"] == "COVERED") & poa["resolved_observation_state"].isin(USABLE)
    days = pd.DataFrame({"window_meter_ok": ok_m.groupby(win["local_reporting_date"]).all(),
                         "window_poa_ok": ok_p.groupby(poa["local_reporting_date"]).all(),
                         "day_length_h": meter.groupby("local_reporting_date")["local_day_length_h"].first()})
    days["has_any_data"] = meter.groupby("local_reporting_date")["coverage_status"].apply(
        lambda x: (x == "COVERED").any())
    days = days.reset_index().rename(columns={"index": "local_reporting_date"})
    d = pd.to_datetime(days["local_reporting_date"])
    in_eval = (days["local_reporting_date"] >= ev["start_local_date"]) & (days["local_reporting_date"] < ev["end_local_date"])
    present = set(days.loc[days["has_any_data"], "local_reporting_date"])
    nb = d.apply(lambda x: (x - pd.Timedelta(days=1)).strftime("%Y-%m-%d") in present and
                 (x + pd.Timedelta(days=1)).strftime("%Y-%m-%d") in present)
    rules = exp["window_selection"]["eligibility"]
    days["excl_not_in_eval_period"] = ~in_eval
    days["excl_dst_transition_day"] = days["day_length_h"] != 24
    days["excl_incomplete_window_meter"] = ~days["window_meter_ok"].fillna(False).astype(bool)
    days["excl_incomplete_window_poa"] = ~days["window_poa_ok"].fillna(False).astype(bool)
    days["excl_missing_neighbour_day"] = ~nb
    excl_cols = [c for c in days.columns if c.startswith("excl_")]
    days["eligible"] = ~days[excl_cols].any(axis=1)
    days = days[in_eval].copy()
    month = days["local_reporting_date"].str.slice(5, 7).astype(int)
    days["season"] = month.map(rules["season_of_month"])
    days.to_csv(OUT / "evaluation_structural_eligibility.csv", index=False)
    return days


def draw_sample(days, exp):
    """Seeded, season-stratified, disjoint draw of window days per fault family."""
    ws = exp["window_selection"]
    rng = np.random.default_rng(ws["sampling_seed"])
    pool = days[days["eligible"]].sort_values("local_reporting_date")
    taken, rows = set(), []
    for fam in ws["families_in_draw_order"]:
        for season in ws["seasons"]:
            cand = pool[(pool["season"] == season) & ~pool["local_reporting_date"].isin(taken)]
            n = ws["windows_per_family_per_season"]
            pick = cand.iloc[np.sort(rng.choice(len(cand), size=min(n, len(cand)), replace=False))]
            for i, dte in enumerate(pick["local_reporting_date"]):
                taken.add(dte)
                rows.append({"family": fam, "season": season, "local_reporting_date": dte,
                             "window_id": f"{fam}-{season}-{i + 1:02d}"})
    sample = pd.DataFrame(rows)
    sample.to_csv(OUT / "evaluation_window_sample.csv", index=False)
    return sample


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    comp, tim_summary, eff, zr = development(EXP)
    days = evaluation_eligibility(EXP)
    sample = draw_sample(days, EXP)
    excl = {c: int(days[c].sum()) for c in days.columns if c.startswith("excl_")}
    summary = {
        "timing_development": tim_summary,
        "zero_policy_pr_effect_on_basic_passed_days": {
            "days_with_any_zero": len(eff),
            "max_zero_share_of_h": float(eff["zero_share_of_h"].max()) if len(eff) else 0.0,
            "median_pp_difference": float(eff["pp_difference_vs_without_zeros"].median()) if len(eff) else 0.0,
            "max_abs_pp_difference": float(eff["pp_difference_vs_without_zeros"].abs().max()) if len(eff) else 0.0},
        "evaluation_structural_eligibility": {
            "days_in_period": len(days), "eligible": int(days["eligible"].sum()), "exclusions_overlapping": excl,
            "eligible_by_season": days[days["eligible"]].groupby("season").size().to_dict()},
        "evaluation_sample": {"unique_windows": int(sample["local_reporting_date"].nunique()),
                              "by_family_season": {f"{f}/{s}": int(n) for (f, s), n in
                                                   sample.groupby(["family", "season"]).size().items()}},
        "inspected_in_evaluation_period": "Phase 2 coverage_status, resolved_observation_state, local_day_length_h "
                                          "and solar geometry only; no values, PR, flags or detector outputs",
    }
    (OUT / "development_analysis_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(comp.iloc[:3, :8].to_string(index=False))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
