"""Phase 8 before-and-after figure for case flatline_poa-DJF-07|primary:hold_last|+240 (2023-01-14).

Reconstructs plotting data from saved inputs only (no detector run):
  - original POA and AC power: Phase 2 observations for the local day;
  - faulted POA: original with the truth record's block set to its hold value;
  - PR, statuses and stale-run evidence: results/phase7/run1 saved outputs.
Writes presentation/figure_flatline_example.png (150 dpi) and .svg, plus
results/phase8/figure_data.csv and figure_checks.json.

Usage (system Python with matplotlib; the frozen .venv is not modified):
    python src/phase8_figure.py
"""
import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASE = "flatline_poa-DJF-07|primary:hold_last|+240"
DAY, TZ = "2023-01-14", "America/Los_Angeles"
SURFACE, INK, INK2, GRID, BAND = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e2dd", "#efe9e2"
ORIGINAL, FAULTED = "#2a78d6", "#eb6834"      # categorical slots 1-2 (validated light-mode pair)
NL = "\n"


def load():
    truth = pd.read_csv(ROOT / "results/phase7/run1/injection_truth.csv")
    t = truth[truth["case_id"] == CASE].iloc[0]
    scores = pd.read_csv(ROOT / "results/phase7/run1/case_scores.csv")
    s = scores[scores["case_id"] == CASE].set_index("method")
    checks = pd.read_parquet(ROOT / "results/phase7/run1/detector_check_results.parquet")
    i2 = checks[(checks["run_key"] == t["run_key"]) & (checks["method"] == "improved")
                & (checks["check_id"] == "I2")].iloc[0]
    obs = pd.read_parquet(ROOT / "data/processed/observations.parquet",
                          columns=["observation_id", "signal", "interval_start_utc", "standardized_value",
                                   "observation_state"])
    lo = pd.Timestamp(DAY).tz_localize(TZ).tz_convert("UTC")
    hi = (pd.Timestamp(DAY) + pd.Timedelta(days=1)).tz_localize(TZ).tz_convert("UTC")
    o = obs[(obs["interval_start_utc"] >= lo) & (obs["interval_start_utc"] < hi)
            & ~obs["observation_state"].isin(["EXCLUDED_BY_SCOPE", "EXPECTED_ROW_MISSING"])]
    return t, s, i2, o


def main():
    t, s, i2, o = load()
    start, end = pd.Timestamp(t["block_start_utc"]), pd.Timestamp(t["block_end_utc"])
    hold = float(t["hold_value"])
    poa = o[o["signal"] == "poa_irradiance"].sort_values("interval_start_utc").copy()
    ac = o[o["signal"] == "ac_power"].sort_values("interval_start_utc").copy()
    in_block = (poa["interval_start_utc"] >= start) & (poa["interval_start_utc"] < end)
    poa["faulted_wm2"] = poa["standardized_value"].where(~in_block, hold)
    stale = json.loads(i2["evidence"])["stale_runs"][0]
    checks = {"case_id": CASE, "block_start_utc": str(start), "block_end_utc": str(end), "hold_value_wm2": hold,
              "rows_in_block": int(in_block.sum()), "truth_n_rows_affected": int(t["n_rows_affected"]),
              "block_rows_equal_truth_affected_ids":
                  set(poa.loc[in_block, "observation_id"]) == set(str(t["affected_observation_ids"]).split(";")),
              "pr_original": float(s.loc["basic", "diagnostic_pr_original"]),
              "pr_faulted": float(s.loc["basic", "diagnostic_pr_faulted"]),
              "pr_same_for_both_methods": bool(s.loc["basic", "diagnostic_pr_faulted"]
                                               == s.loc["improved", "diagnostic_pr_faulted"]),
              "basic_status": s.loc["basic", "faulted_status"], "basic_category": s.loc["basic", "category"],
              "improved_status": s.loc["improved", "faulted_status"],
              "improved_reasons": s.loc["improved", "faulted_reasons"],
              "improved_category": s.loc["improved", "category"],
              "stale_run_readings": int(stale["readings"]), "stale_run_span_min": int(stale["span_min"])}
    assert checks["rows_in_block"] == checks["truth_n_rows_affected"] == 48
    assert checks["block_rows_equal_truth_affected_ids"]

    poa["local"] = poa["interval_start_utc"].dt.tz_convert(TZ).dt.tz_localize(None) + pd.Timedelta("2min30s")
    ac["local"] = ac["interval_start_utc"].dt.tz_convert(TZ).dt.tz_localize(None) + pd.Timedelta("7min30s")
    poa_d = poa[(poa["local"].dt.hour >= 6) & (poa["local"].dt.hour < 18)]
    ac_d = ac[(ac["local"].dt.hour >= 6) & (ac["local"].dt.hour < 18)]
    ls, le = (x.tz_convert(TZ).tz_localize(None) for x in (start, end))
    pr0, pr1 = 100 * checks["pr_original"], 100 * checks["pr_faulted"]  # noqa: F841

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "svg.fonttype": "none"})
    fig = plt.figure(figsize=(13, 7.8), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.35, 1], hspace=0.42, left=0.065, right=0.675, top=0.775,
                          bottom=0.185)
    ax1, ax2 = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[1, 0])
    fig.text(0.065, 0.96, f"A frozen irradiance sensor inflated reported PR from {pr0:.1f}% to {pr1:.1f}%",
             fontsize=19, fontweight="bold", color=INK)
    fig.text(0.065, 0.91, f"PVDAQ system 2107, 2023-01-14. SYNTHETIC fault: plane-of-array (POA) irradiance held at its "
             f"last value ({hold:.1f} W/m²) for 240 minutes.{NL}AC power is unchanged. Diagnostic PR uses the same "
             "arithmetic for both methods; only the validation checks differ.", fontsize=11.5, color=INK2, va="top")

    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        ax.axvspan(ls, le, color=BAND, zorder=0, lw=0)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(length=0)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.set_xlim(poa_d["local"].min(), poa_d["local"].max())
    ax1.plot(poa_d["local"], poa_d["standardized_value"], color=ORIGINAL, lw=2)
    blk = poa_d[(poa_d["local"] >= ls) & (poa_d["local"] < le)]
    ax1.plot(blk["local"], blk["faulted_wm2"], color=FAULTED, lw=2.6, solid_capstyle="round")
    ax1.set_ylabel("POA irradiance (W/m²)")
    ax1.set_ylim(0, poa_d["standardized_value"].max() * 1.18)
    ax1.set_title("Plane-of-array irradiance: recorded vs synthetic flatline", loc="left", fontsize=12.5, color=INK)
    ax1.text(ls + (le - ls) / 2, ax1.get_ylim()[1] * 0.985, "synthetic fault window (240 min)", ha="center",
             va="top", fontsize=11, color=INK2)
    ax1.annotate(f"held at {hold:.1f} W/m²", xy=(ls + (le - ls) * 0.80, hold),
                 xytext=(ls + (le - ls) * 0.60, ax1.get_ylim()[1] * 0.55), color=FAULTED, fontsize=11.5,
                 fontweight="bold", arrowprops={"arrowstyle": "-", "color": FAULTED, "lw": 1.2})
    leg = [plt.Line2D([], [], color=ORIGINAL, lw=2, label="Recorded POA"),
           plt.Line2D([], [], color=FAULTED, lw=2.6, label="Faulted POA (synthetic)"),
           Patch(facecolor=BAND, label="Synthetic fault window")]
    ax1.legend(handles=leg, loc="upper left", frameon=False, fontsize=10.5, labelcolor=INK2)

    ax2.plot(ac_d["local"], ac_d["standardized_value"], color=INK2, lw=2)
    ax2.set_ylabel("AC power (kW)")
    ax2.set_ylim(0, ac_d["standardized_value"].max() * 1.2)
    ax2.set_title("AC power from the revenue meter: unchanged by the fault", loc="left", fontsize=12.5, color=INK)
    ax2.set_xlabel("Local time (America/Los_Angeles)")

    x = 0.715
    fig.text(x, 0.775, "Diagnostic PR for the day", fontsize=13, color=INK2, va="top")
    fig.text(x, 0.735, f"{pr0:.1f}%", fontsize=30, color=ORIGINAL, va="top", fontweight="bold")
    fig.text(x, 0.655, f"with recorded POA{NL}(comparison reference, not verified{NL}true performance)",
             fontsize=10.5, color=INK2, va="top")
    fig.text(x, 0.56, f"{pr1:.1f}%", fontsize=30, color=FAULTED, va="top", fontweight="bold")
    fig.text(x, 0.48, f"with faulted POA: +{pr1 - pr0:.1f} percentage points{NL}(the irradiance denominator falls,"
             f"{NL}so PR is inflated)", fontsize=10.5, color=INK2, va="top")
    fig.add_artist(plt.Line2D([x, 0.975], [0.375, 0.375], color=GRID, lw=1, transform=fig.transFigure))
    fig.text(x, 0.355, "How each method responds", fontsize=13, color=INK2, va="top")
    fig.text(x, 0.30, "Basic checks", fontsize=12.5, color=INK, va="top", fontweight="bold")
    fig.text(x, 0.262, f"✗  Fault not caught: {checks['basic_status']}{NL}     PR {pr1:.1f}% would be reported", fontsize=11.5,
             color=INK, va="top")
    fig.text(x, 0.185, "Improved checks", fontsize=12.5, color=INK, va="top", fontweight="bold")
    fig.text(x, 0.147, f"✓  Fault caught: {checks['improved_status']}{NL}     POA_STALE: {checks['stale_run_readings']} "
             f"identical readings{NL}     (PR withheld for review)", fontsize=11.5, color=INK, va="top")
    fig.text(0.065, 0.085, "The basic method checks input quality and completeness but includes no PR plausibility "
             "rule.", fontsize=10.5, color=INK, va="top")
    fig.text(0.065, 0.055, "Independent experiment on public NREL PVDAQ data with a controlled, synthetic fault. "
             f"One illustrative case, not a production result.{NL}Source: results/phase7/run1, case {CASE}.",
             fontsize=9.5, color=INK2, va="top")

    out = ROOT / "presentation"
    out.mkdir(exist_ok=True)
    fig.savefig(out / "figure_flatline_example.png", dpi=150, facecolor=SURFACE)
    fig.savefig(out / "figure_flatline_example.svg", facecolor=SURFACE)
    (ROOT / "results/phase8").mkdir(parents=True, exist_ok=True)
    pd.concat([poa_d.assign(series="poa")[["series", "observation_id", "interval_start_utc", "standardized_value",
                                            "faulted_wm2"]],
               ac_d.assign(series="ac_power")[["series", "observation_id", "interval_start_utc",
                                               "standardized_value"]]]).to_csv(ROOT / "results/phase8/figure_data.csv",
                                                                               index=False)
    checks.update({"matplotlib": matplotlib.__version__, "png_px": [13 * 150, round(7.8 * 150)],
                   "min_font_pt": 9.5,
                   "title": f"A frozen irradiance sensor inflated reported PR from {pr0:.1f}% to {pr1:.1f}%",
                   "basic_limitation_note": True})
    (ROOT / "results/phase8/figure_checks.json").write_text(json.dumps(checks, indent=2, default=str), encoding="utf-8")
    print(json.dumps(checks, indent=2, default=str))


if __name__ == "__main__":
    main()
