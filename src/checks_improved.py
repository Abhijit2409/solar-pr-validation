"""Improved-only checks (experiment-v2): I1 midday completeness, I2 POA stale, I3 timing."""
import numpy as np
import pandas as pd
import pvlib

from checks_basic import FLAGGED, INCONCLUSIVE, PASS, USABLE, result


def midday_completeness(day, dm, cfg):
    thr = cfg["methods"]["improved"]["midday_completeness"]["min_clearsky_weighted_coverage"]
    if dm["paired_intervals"] == 0:
        return result(day, "I1", "improved_only", INCONCLUSIVE, "LOW_CLEARSKY_WEIGHTED_COVERAGE", "NONE", None,
                      f">= {thr}", "fraction", evidence={"note": "not evaluated: no paired data"})
    cov = dm["clearsky_weighted_coverage"]
    return result(day, "I1", "improved_only", PASS if cov >= thr else FLAGGED, "LOW_CLEARSKY_WEIGHTED_COVERAGE",
                  "INSUFFICIENT_DATA", round(cov, 6), f">= {thr}", "fraction of window clear-sky irradiation",
                  evidence={"model_used_as": "weight only"})


def stale_runs(poa_day, tol):
    """Runs of consecutive usable in-window POA readings with |delta| <= tol (counted in readings)."""
    p = poa_day[poa_day["in_window"].fillna(False).astype(bool)].sort_values("interval_start_utc").reset_index(drop=True)
    usable = p["state"].isin(USABLE).to_numpy()
    vals = p["value"].to_numpy(dtype=float)
    starts = p["interval_start_utc"].to_numpy()
    runs, cur = [], [0] if len(p) and usable[0] else []
    for i in range(1, len(p)):
        contig = starts[i] - starts[i - 1] == np.timedelta64(5, "m")
        same = usable[i] and usable[i - 1] and contig and abs(vals[i] - vals[i - 1]) <= tol + 1e-9
        if same:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i] if usable[i] else []
    if cur:
        runs.append(cur)
    return [(p.loc[r, "interval_start_utc"].tolist(), float(vals[r[0]])) for r in runs]


def poa_stale(day, poa_day, cfg):
    s = cfg["methods"]["improved"]["poa_stale"]
    runs = stale_runs(poa_day, s["tolerance_wm2"])
    longest = max((len(r[0]) for r in runs), default=0)
    hit = [r for r in runs if len(r[0]) >= s["min_run_values"]]
    affected = [t for r in hit for t in r[0]]
    return result(day, "I2", "improved_only", FLAGGED if hit else PASS, "POA_STALE", "REVIEW_REQUIRED", longest,
                  f">= {s['min_run_values']} readings within {s['tolerance_wm2']} W/m2", "readings (x 5 min span)",
                  affected, {"stale_runs": [{"readings": len(r[0]), "span_min": 5 * len(r[0]), "value": r[1],
                                             "start": str(r[0][0])} for r in hit]})


class ClearSkyMinutes:
    """1-minute clear-sky POA (cumulative sums) for lag evaluation."""

    def __init__(self, site, t0, t1):
        self.mins = pd.date_range(t0, t1, freq="1min")
        loc = pvlib.location.Location(site["latitude"], site["longitude"], tz="UTC", altitude=site["elevation_m"])
        sp = loc.get_solarposition(self.mins)
        cs = loc.get_clearsky(self.mins, model="ineichen", solar_position=sp)
        mod = pvlib.irradiance.get_total_irradiance(site["tilt_deg"], site["azimuth_deg"], sp["apparent_zenith"],
                                                    sp["azimuth"], cs["dni"], cs["ghi"], cs["dhi"])["poa_global"]
        self.csum = np.concatenate([[0.0], np.cumsum(mod.fillna(0).clip(lower=0).to_numpy())])

    def index(self, starts):
        return ((pd.DatetimeIndex(starts) - self.mins[0]) / pd.Timedelta("1min")).to_numpy().astype(int)

    def mean5(self, b):
        return (self.csum[b + 5] - self.csum[b]) / 5


def best_lag(corr, lags):
    """Deterministic argmax: among maxima (rounded to 12 decimals) choose smallest |k|, then smallest k."""
    finite = np.isfinite(corr)
    if not finite.any():
        return None, float("nan")
    r = np.round(np.where(finite, corr, -np.inf), 12)
    cand = lags[r == r.max()]
    k = int(sorted(cand, key=lambda x: (abs(x), x))[0])
    return k, float(corr[list(lags).index(k)])


def timing(day, poa_day, csm, cfg, baseline):
    t = cfg["methods"]["improved"]["timing"]
    p = poa_day[poa_day["in_window"].fillna(False).astype(bool) & poa_day["state"].isin(USABLE)]
    b = csm.index(p["interval_start_utc"])
    meas = p["value"].to_numpy(dtype=float)
    mod0 = csm.mean5(b)
    use = mod0 > t["min_model_wm2"]
    b, meas, mod0 = b[use], meas[use], mod0[use]
    lags = np.arange(-t["search_range_min"], t["search_range_min"] + 1)
    ev = {"n_intervals": int(use.sum()), "baseline_lag_min": baseline, "latency": "retrospective, after day end"}
    thr = f"|lag - baseline| >= {t['threshold_min']} on a conclusive day"

    def inconclusive(why, **extra):
        ev.update(extra, inconclusive_reason=why)
        return result(day, "I3", "improved_only", INCONCLUSIVE, "TIMING_INCONCLUSIVE", "CONTEXT",
                      extra.get("best_lag_min"), thr, "min", evidence=ev)

    if use.sum() < t["min_intervals"]:
        return inconclusive("fewer than min_intervals eligible intervals")
    x = meas - meas.mean()
    if not np.any(x != 0):
        return inconclusive("measured series constant: correlation undefined")
    model = (csm.csum[b[None, :] + lags[:, None] + 5] - csm.csum[b[None, :] + lags[:, None]]) / 5
    y = model - model.mean(axis=1, keepdims=True)
    denom = np.sqrt((x * x).sum() * (y * y).sum(axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.where(denom > 0, (y @ x) / denom, np.nan)
    k, c = best_lag(corr, lags)
    if k is None:
        return inconclusive("correlation undefined at every lag")
    ratio = float(meas.sum() / mod0.sum())
    ev.update(best_lag_min=k, corr_at_best=round(c, 6), energy_ratio=round(ratio, 4))
    if abs(k) == t["search_range_min"]:
        return inconclusive("best lag at search boundary", best_lag_min=k)
    if c < t["min_corr"] or not (t["energy_ratio_range"][0] <= ratio <= t["energy_ratio_range"][1]):
        return inconclusive("not a clear day (corr or energy ratio gate)", best_lag_min=k)
    dev = k - baseline
    ev["lag_minus_baseline_min"] = dev
    return result(day, "I3", "improved_only", FLAGGED if abs(dev) >= t["threshold_min"] else PASS,
                  "TIMING_INCONSISTENT", "REVIEW_REQUIRED", k, thr, "min", evidence=ev)
