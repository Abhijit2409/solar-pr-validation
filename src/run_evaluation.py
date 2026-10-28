"""Evaluation runner (experiment-v3).

Commands:
  smoke          development-period smoke cases (labelled; never the frozen evaluation windows)
  freeze         create implementation freeze B (results/phase6/implementation_freeze_manifest.json)
  verify-freeze  verify every frozen file, input, processed table and package version
  evaluate       FINAL evaluation of the 96 frozen windows (Phase 7 only). Verifies freeze B first,
                 refuses to write into an existing run directory, keeps failures and unscorable cases.

Detector outputs are keyed by an opaque run_key and saved separately from injection truth;
the detector receives only detector-input observations.

Usage:
  python src/run_evaluation.py smoke
  python src/run_evaluation.py freeze [--force]
  python src/run_evaluation.py verify-freeze
  python src/run_evaluation.py evaluate --run-id run1
"""
import argparse
import json
import sys
import traceback
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect as D  # noqa: E402
import detector_inputs as DI  # noqa: E402
import evaluate as EV  # noqa: E402
import evidence as E  # noqa: E402
import freeze_b as FB  # noqa: E402
import ingest  # noqa: E402
import inject_faults as INJ  # noqa: E402

ROOT = ingest.ROOT
PROC = ROOT / "data" / "processed"
FREEZE = "results/phase6/implementation_freeze_manifest.json"
SMOKE_TARGETS = ["2019-01-15", "2019-04-15", "2019-07-15", "2019-10-15"]   # development dates (smoke only)


def context():
    exp = yaml.safe_load((ROOT / "config/experiment_v3.yaml").read_text(encoding="utf-8"))
    mcfg = yaml.safe_load((ROOT / "config/metric_definition.yaml").read_text(encoding="utf-8"))
    site = pd.read_parquet(PROC / "site_metadata.parquet").iloc[0].to_dict()
    meta = E.measurement_meta(ingest.load_config())
    summ = json.loads((ROOT / "results/phase4/development_analysis_summary.json").read_text(encoding="utf-8"))
    baseline = summ["timing_development"]["baseline_median_lag_min"]
    if baseline != exp["methods"]["improved"]["timing"]["baseline_lag_min"]:
        raise SystemExit("timing baseline differs from configuration")
    return exp, mcfg, site, meta, baseline


def shift_date(day, k):
    return (pd.Timestamp(day) + pd.Timedelta(days=k)).strftime("%Y-%m-%d")


class RunKeys:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return f"run{self.n:06d}"


def run_detector(det_input, day, ctx, run_key, store):
    exp, mcfg, site, meta, baseline = ctx
    checks, days, _ = D.evaluate(det_input, site, meta, mcfg, exp, baseline, [day])
    store["checks"].append(checks.assign(run_key=run_key))
    store["days"].append(days.assign(run_key=run_key))
    return checks, days


def run_window(window_id, family, day, ctx, keys, store):
    """Unmodified counterpart + every case of the family for one window."""
    exp, mcfg, site, meta, _ = ctx
    lo, hi = exp["injection"]["context_days"]
    obs, lineage = DI.load_detector_input(PROC, shift_date(day, lo), shift_date(day, hi), site["local_timezone"])
    plan = INJ.plan_window(window_id, family, day, site, mcfg, exp)
    det0, _ = INJ.reassign_refs(obs)
    k0 = keys.next()
    c0, d0 = run_detector(det0, day, ctx, k0, store)
    store["unmodified"].append({"window_id": window_id, "family": family, "local_reporting_date": day, "run_key": k0})
    smap = EV.specific_map(exp)
    lin = dict(zip(lineage["obs_ref"], lineage["observation_id"]))
    for scenario, severity in INJ.case_specs(family, exp):
        faulted, truth = INJ.apply_case(obs, plan, scenario, severity, site, meta, exp)
        truth["affected_observation_ids"] = ";".join(lin.get(r, "") for r in truth["affected_refs"].split(";") if r)
        cf = df = None
        if truth["injection_status"] == INJ.APPLIED:
            detf, _ = INJ.reassign_refs(faulted)
            truth["run_key"] = keys.next()
            cf, df = run_detector(detf, day, ctx, truth["run_key"], store)
        else:
            truth["run_key"] = ""
        truth["counterpart_run_key"] = k0
        store["truth"].append(truth)
        for method in ("basic", "improved"):
            o = d0[d0["method"] == method].iloc[0].to_dict()
            f = df[df["method"] == method].iloc[0].to_dict() if df is not None else None
            s = EV.score_case(method, family, truth["injection_status"], o, f,
                              c0[c0["method"] == method], cf[cf["method"] == method] if cf is not None else None, smap)
            s.update({k: truth[k] for k in ("case_id", "window_id", "family", "scenario", "severity",
                                             "local_reporting_date")})
            store["scores"].append(s)


def finish(store, out, windows, season_of):
    out.mkdir(parents=True, exist_ok=True)
    pd.concat(store["days"]).to_csv(out / "detector_day_results.csv", index=False)
    pd.concat(store["checks"]).astype({"measured": str, "threshold": str}).to_parquet(
        out / "detector_check_results.parquet", index=False)
    truth = pd.DataFrame(store["truth"])
    truth.to_csv(out / "injection_truth.csv", index=False)
    pd.DataFrame(store["unmodified"]).to_csv(out / "unmodified_counterparts.csv", index=False)
    scores = pd.DataFrame(store["scores"])
    scores["season"] = scores["window_id"].map(season_of)
    scores["scenario_class"] = scores["scenario"].str.split(":").str[0]
    scores.to_csv(out / "case_scores.csv", index=False)
    summaries = []
    for by in (["method", "family", "scenario"], ["method", "family", "scenario", "severity"],
               ["method", "family", "scenario", "season"]):
        s = EV.summarize(scores, by)
        s.insert(0, "grouping", "+".join(by))
        summaries.append(s)
    pd.concat(summaries, ignore_index=True).to_csv(out / "rate_summary.csv", index=False)
    days = pd.concat(store["days"])
    unmod = days[days["run_key"].isin(pd.DataFrame(store["unmodified"])["run_key"])]
    EV.alert_burden(unmod, "sampled windows (unique unmodified days)").to_csv(out / "alert_burden_sampled.csv",
                                                                               index=False)
    if store["failures"]:
        pd.DataFrame(store["failures"]).to_csv(out / "failures.csv", index=False)
    return scores


def new_store():
    return {"checks": [], "days": [], "truth": [], "scores": [], "unmodified": [], "failures": []}


def cmd_smoke(_args):
    ctx = context()
    exp = ctx[0]
    dev_lo, dev_hi = exp["periods"]["development"]["start_local_date"], exp["periods"]["development"]["end_local_date"]
    dev = pd.read_csv(ROOT / "results/phase5/development_method_results.csv")
    complete = set(dev.loc[(dev["method"] == "basic") & (dev["time_coverage"] == 1.0), "local_reporting_date"])
    days = []
    for t in SMOKE_TARGETS:
        assert dev_lo <= t < dev_hi
        days.append(next(d for d in sorted(complete) if d >= t))
    keys, store, windows = RunKeys(), new_store(), []
    for day in days:
        for family in ("missing_daytime", "timestamp_shift", "flatline_poa"):
            wid = f"smoke-{family}-{day}"
            windows.append({"window_id": wid, "family": family, "local_reporting_date": day})
            run_window(wid, family, day, ctx, keys, store)
    season_of = {w["window_id"]: exp["window_selection"]["eligibility"]["season_of_month"][int(w["local_reporting_date"][5:7])]
                 for w in windows}
    out = ROOT / "results/phase6/smoke"
    scores = finish(store, out, windows, season_of)
    (out / "smoke_metadata.json").write_text(json.dumps(
        {"label": "DEVELOPMENT-PERIOD SMOKE CASES (not evaluation)", "days": days, "windows": len(windows),
         "detector_runs": keys.n, "cases": len(scores) // 2, "experiment_version": exp["experiment_version"]},
        indent=2), encoding="utf-8")
    print(scores.groupby(["method", "family", "category"]).size().to_string())


def cmd_freeze(args):
    path = ROOT / FREEZE
    if path.exists() and not args.force:
        raise SystemExit(f"{FREEZE} exists; use --force to recreate")
    m = FB.create(ROOT)
    path.write_text(json.dumps(m, indent=2), encoding="utf-8")
    print(f"freeze B written: {len(m['file_sha256'])} files, {len(m['processed_file_sha256'])} processed tables")


def cmd_verify(_args):
    problems = FB.verify(ROOT, FB.load(ROOT, FREEZE))
    if problems:
        print("FREEZE B VERIFICATION FAILED:")
        print("\n".join(problems))
        raise SystemExit(1)
    print("freeze B verified: all frozen files, processed tables and versions match")


def cmd_evaluate(args):
    problems = FB.verify(ROOT, FB.load(ROOT, FREEZE))
    if problems:
        raise SystemExit("refusing to evaluate; freeze B mismatch:\n" + "\n".join(problems))
    out = ROOT / "results/phase7" / args.run_id
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing run directory {out} (completed or failed runs are kept)")
    out.mkdir(parents=True)
    started = pd.Timestamp.now(tz="UTC")
    ctx = context()
    exp = ctx[0]
    sample = pd.read_csv(ROOT / "results/phase4/evaluation_window_sample.csv")
    keys, store = RunKeys(), new_store()
    for w in sample.itertuples():
        try:
            run_window(w.window_id, w.family, w.local_reporting_date, ctx, keys, store)
        except Exception:                                   # preserve the failure, continue with other windows
            store["failures"].append({"window_id": w.window_id, "error": traceback.format_exc()})
    season_of = dict(zip(sample["window_id"], sample["season"]))
    scores = finish(store, out, sample, season_of)
    elig = pd.read_csv(ROOT / "results/phase4/evaluation_structural_eligibility.csv")
    elig_days = sorted(elig.loc[elig["eligible"], "local_reporting_date"])
    ev = exp["periods"]["evaluation"]
    obs, _ = DI.load_detector_input(PROC, ev["start_local_date"], ev["end_local_date"], ctx[2]["local_timezone"])
    det, _ = INJ.reassign_refs(obs)
    _, pop_days, _ = D.evaluate(det, ctx[2], ctx[3], ctx[1], exp, ctx[4], elig_days)
    pop_days.to_csv(out / "population_unmodified_days.csv", index=False)
    EV.alert_burden(pop_days, "full eligible population (unique days)").to_csv(
        out / "alert_burden_population.csv", index=False)
    meta = {"experiment_version": exp["experiment_version"], "started_utc": started.isoformat(),
            "finished_utc": pd.Timestamp.now(tz="UTC").isoformat(), "windows": len(sample),
            "detector_runs": keys.n, "cases": len(scores) // 2, "failures": len(store["failures"]),
            "freeze_manifest_sha256": FB.sha256(ROOT / FREEZE), "versions": FB.versions()}
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out / "COMPLETED").write_text(meta["finished_utc"], encoding="utf-8")
    print(json.dumps(meta, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke")
    f = sub.add_parser("freeze")
    f.add_argument("--force", action="store_true")
    sub.add_parser("verify-freeze")
    e = sub.add_parser("evaluate")
    e.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    {"smoke": cmd_smoke, "freeze": cmd_freeze, "verify-freeze": cmd_verify, "evaluate": cmd_evaluate}[args.cmd](args)


if __name__ == "__main__":
    main()
