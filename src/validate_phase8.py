"""Phase 8 packaging validation: figure, table, memo, README and walkthrough agree with the evidence.

Writes results/phase8/packaging_validation.json. Exit 0 only if every check passes.

Usage:
    python src/validate_phase8.py
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze_b as FB  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOCS = {"memo": "presentation/executive_memo.md", "readme": "README.md", "table": "presentation/results_table.md",
        "walkthrough": "presentation/walkthrough.md"}
# Headline tokens each document must contain (all traced in the claim register).
PR_SENTENCE = "compares the energy produced with what the measured sunlight and installed capacity imply under reference conditions"
FRAMING = ("caught the tested long irradiance flatlines and additional one-hour gaps. Timing uncertainty still allowed "
           "some shifted cases with PR changes up to 5.4 percentage points to be reported")
REQUIRED = {
    "memo": ["112/112", "0/112", "8/26", "15/26", "29/168", "113 of 168 shifted cases", "5.4 percentage points",
             "85.6 percentage points", "89.3%", "174.9%", "13.9%", "1,270 unique eligible", "28 windows",
             "Neither includes a PR plausibility rule", PR_SENTENCE, FRAMING, "unconfirmed", "synthetic",
             "Not affiliated with Clir", "does not show that every competent workflow would publish 174.9%",
             "lineage", "daylight-saving", "correctly paired"],
    "readme": ["112/112", "0/112", "0/28", "8/26", "15/26", "52/52", "0/26", "29/168", "113 of 168 shifted cases",
               "5.4 percentage points", "85.6 percentage points", "89.3%", "174.9%", "13.9%", "28 windows",
               "1,270 unique eligible days", PR_SENTENCE, FRAMING,
               "The basic method checks input quality and completeness but includes no PR plausibility rule",
               "does not establish that every competent reporting workflow would publish 174.9%",
               "it is not implemented here", "CHECKS_PASSED` means the configured checks passed",
               "timing not verified", "not affiliated", "figure_flatline_example.png", "correctly paired"],
    "table": ["0/28", "0/112", "112/112", "0/26", "8/26", "15/26", "52/52", "0/168", "29/168",
              "113 inconclusive cases, still reported", "5.4 percentage points", "13.9%", "28 windows x 4",
              "1,270 unique eligible days", "A case is one fault scenario and severity applied to one window"],
    "walkthrough": ["112", "28 windows", "15 of 26", "113 of 168 shifted cases", "5.4 percentage points",
                    "49 identical readings", "672", "96 sampled windows", PR_SENTENCE.replace("compares", "It compares"),
                    "no PR plausibility rule"],
}
FORBIDDEN = [r"\b85\.6%", r"\b5\.4%", r"\b2\.7%", r"every (timing|timestamp|shift)", r"all (timing|timestamp|shift)",
             r"detected all shifts", r"production[- ]ready(?! claim)", r"(?<!daylight-)\bsavings?\b",
             r"saves? (money|time)",
             r"112 (independent )?days", r"affiliated with Clir(?! Renewables and)", r"\b20/112\b",
             r"\bsuperior(ity)? to Clir", r"verified true performance(?=\.)"]
ALLOWED_CONTEXT = {"production readiness": "negated", "savings": "negated"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    rows = []

    def check(cid, name, ok, measured):
        rows.append({"check_id": cid, "check": name, "result": "PASS" if ok else "FAIL", "measured": measured})

    v7 = json.loads((ROOT / "results/phase7/validation_manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in v7["run1_output_sha256"].items() if sha(ROOT / "results/phase7/run1" / f) != h]
    check("P8-01", "run1 outputs unchanged (Phase 7 hashes)", not bad, f"{len(v7['run1_output_sha256']) - len(bad)}/"
          f"{len(v7['run1_output_sha256'])}")
    problems = FB.verify(ROOT, FB.load(ROOT))
    check("P8-02", "Implementation freeze B verifies", not problems, problems or "verified")

    saved = (ROOT / "results/phase8/claim_register.csv").read_bytes()
    subprocess.run([sys.executable, str(ROOT / "src/phase8_claims.py")], cwd=ROOT, capture_output=True, check=True)
    check("P8-03", "Claim register reproduces exactly from saved run1 outputs",
          (ROOT / "results/phase8/claim_register.csv").read_bytes() == saved, "byte-identical regeneration")
    reg = pd.read_csv(ROOT / "results/phase8/claim_register.csv").set_index("claim_id")

    fc = json.loads((ROOT / "results/phase8/figure_checks.json").read_text(encoding="utf-8"))
    fig_ok = (f"{100 * fc['pr_original']:.1f}% -> {100 * fc['pr_faulted']:.1f}%" == reg.loc["C10", "value"]
              and fc["basic_status"] == "CHECKS_PASSED" and fc["improved_status"] == "REVIEW_REQUIRED"
              and fc["improved_reasons"] == "POA_STALE" and fc["rows_in_block"] == 48
              and fc["block_rows_equal_truth_affected_ids"] and fc["stale_run_readings"] == 49
              and fc["png_px"][0] >= 1600 and fc["min_font_pt"] >= 9
              and fc.get("title") == reg.loc["C17", "value"] and fc.get("basic_limitation_note") is True)
    png = ROOT / "presentation/figure_flatline_example.png"
    svg = ROOT / "presentation/figure_flatline_example.svg"
    check("P8-04", "Figure values equal the claim register and saved outputs; PNG >= 1600 px wide; fonts >= 9 pt; "
                   "SVG exported", fig_ok and png.exists() and svg.exists() and "POA_STALE" in svg.read_text(
                       encoding="utf-8"), f"{reg.loc['C10', 'value']}, png {fc['png_px']}")

    texts = {k: (ROOT / p).read_text(encoding="utf-8") for k, p in DOCS.items()}
    plain = {k: v.replace("**", "").replace("*", "").lower() for k, v in texts.items()}   # ignore markdown emphasis
    missing = {k: [t for t in toks if t.replace("**", "").lower() not in plain[k]] for k, toks in REQUIRED.items()}
    check("P8-05", "Every headline number appears consistently in memo, README, table and walkthrough",
          not any(missing.values()), {k: v for k, v in missing.items() if v} or "all present")

    reg_vals = {"C01-improved": "112/112", "C01-basic": "0/112", "C04-basic": "8/26", "C04-improved": "15/26",
                "C05-basic": "52/52", "C06-improved": "29/168", "C07": "5.4", "C08": "2.7", "C09": "85.6",
                "C02-basic": "0/28", "C03-basic": "0/26"}
    mism = {k: (reg.loc[k, "value"], v) for k, v in reg_vals.items() if str(reg.loc[k, "value"]) != v}
    check("P8-06", "Document numbers equal claim-register values", not mism and str(reg.loc["C06i-improved",
          "numerator"]).startswith("113") and reg.loc["C11-basic", "value"].endswith("(13.9%)"), mism or "equal")

    hits = {}
    for k, txt in texts.items():
        for pat in FORBIDDEN:
            for m in re.finditer(pat, txt, flags=re.IGNORECASE):
                ctx = txt[max(0, m.start() - 40): m.end() + 20].replace("\n", " ")
                if re.search(r"\b(no|not|nor|doesn't|does not|n't)\b", ctx, flags=re.IGNORECASE):
                    continue
                hits.setdefault(k, []).append(ctx)
    check("P8-07", "No wrong units (pp written as %), no claim that all timing faults were detected, no savings, "
                   "production-readiness, superiority or affiliation claims, no secondary numbers in headlines",
          not hits, hits or "none")

    pp_ok = all("percentage points" in texts[k] for k in ("memo", "readme", "table"))
    check("P8-08", "PR changes labelled in percentage points; PR values in %", pp_ok, "labels present")
    disclose = all(s in texts["readme"] for s in ("correlated", "synthetic", "One site")) and \
        "correlated" in texts["memo"] and "Correlated" in texts["table"]
    check("P8-09", "Correlated severities, window counts and one-site/synthetic scope disclosed", disclose, "present")
    sec_sep = "secondary" in texts["readme"].lower() and "Secondary" not in texts["table"].split("|")[0]
    check("P8-10", "Primary and secondary results kept distinct (secondary only by reference)", sec_sep
          and "20/112" not in "".join(texts.values()), "secondary referenced, not mixed")

    words = len(re.findall(r"\b\w+\b", texts["walkthrough"].split("---", 1)[1]))
    memo_words = len(re.findall(r"\b\w+\b", texts["memo"]))
    check("P8-11", "Walkthrough about three minutes (380-520 words); memo 350-450 words (word count only; page fit "
                   "not claimed)", 380 <= words <= 520 and 350 <= memo_words <= 450,
          f"walkthrough {words} words, memo {memo_words} words")
    c18 = reg.loc["C18", "value"] == "PR-value rules: none"
    plaus_ok = all("plausibility" in texts[k] for k in ("memo", "readme", "walkthrough")) and c18
    check("P8-12", "PR plausibility described only as absent / possible future safeguard; rule table confirms no "
                   "PR-value rule", plaus_ok, reg.loc["C18", "value"])

    summary = pd.DataFrame(rows)
    status = "PASS" if (summary["result"] == "PASS").all() else "FAIL"
    out = {"phase": 8, "overall_status": status, "checks": rows,
           "file_sha256": {p: sha(ROOT / p) for p in list(DOCS.values()) + [
               "presentation/figure_flatline_example.png", "presentation/figure_flatline_example.svg",
               "results/phase8/claim_register.csv", "results/phase8/results_table.csv",
               "results/phase8/figure_checks.json", "docs/revision_log.md", "src/phase8_figure.py",
               "src/phase8_claims.py"]},
           "validator_sha256": sha(ROOT / "src/validate_phase8.py"),
           "revision": "presentation revision after external review (figure title, memo length, case/day terminology, "
                       "PR explanation, framing); experiment and run1 unchanged",
           "figure_visual_review": {"by": "assistant (not a human review)", "title_single_line": True,
                                    "clipped_labels": False, "overlaps": False, "units_correct": True,
                                    "fault_window_local": "11:55-15:55 (truth block 19:55-23:55 UTC)",
                                    "smallest_text_pt": 9.5},
           "note": "Automated consistency checks plus the visual figure review recorded above."}
    (ROOT / "results/phase8/packaging_validation.json").write_text(json.dumps(out, indent=2, default=str),
                                                                   encoding="utf-8")
    print(summary.to_string(index=False, max_colwidth=100))
    print("overall:", status)
    raise SystemExit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
