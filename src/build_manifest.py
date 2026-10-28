"""Build docs/source_manifest.json from data/raw/download_log.jsonl.

Re-hashes every logged file so the manifest also confirms the raw files are
unchanged since download.

Usage:
    python src/build_manifest.py
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "raw" / "download_log.jsonl"
OUT = ROOT / "docs" / "source_manifest.json"

ROLES = {
    "2107_meter_15m_data.csv": "selected site - AC output (primary window)",
    "2107_irradiance_data.csv": "selected site - POA irradiance (primary window)",
    "2107_meter_15m_data_2024.csv": "selected site - AC output (later increment, audited, not in primary window)",
    "2107_irradiance_data_2024.csv": "selected site - POA irradiance (later increment, audited, not in primary window)",
    "2107_meter_15m_data_2025.csv": "selected site - AC output (later increment, audited, not in primary window)",
    "2107_irradiance_15m_data_2025.csv": "selected site - POA irradiance (later increment, audited, not in primary window)",
    "2107_electrical_data_2024.csv": "selected site - inverter channels, used only for the meter boundary check",
    "2107_electrical_data_v1.csv": "selected site - inverter channels 2017-2023 (corrected-header version), boundary and timing check only",
    "2107_system_metadata.json": "selected site - system metadata",
    "systems_20250729.csv": "PVDAQ site index used for candidate search",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    files = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        name = rec["key"].split("/")[-1]
        current = sha256(ROOT / rec["local_path"])
        rec["original_filename"] = name
        rec["role"] = ROLES.get(name, "supporting evidence (candidate metadata, change log, or weather channel)")
        rec["dataset_version"] = ("S3 object Last-Modified " + str(rec["last_modified"])
                                  + "; ETag " + rec["etag"])
        rec["sha256_rechecked"] = current
        rec["unchanged_since_download"] = current == rec["sha256"]
        files.append(rec)
    manifest = {
        "dataset": "Photovoltaic Data Acquisition (PVDAQ) Public Datasets, incl. 2023 DOE Solar Data Prize",
        "publisher": "National Renewable Energy Laboratory (NREL), via Open Energy Data Initiative (OEDI)",
        "doi": "https://doi.org/10.25984/1846021",
        "submission_page": "https://data.openei.org/submissions/4568",
        "submission_last_updated_per_page": "2025-07-31",
        "licence": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), as stated on the OEDI submission page",
        "header_documentation": "https://github.com/openEDI/documentation/blob/main/pvdaq.md",
        "access_method": "Anonymous HTTPS GET from https://oedi-data-lake.s3.amazonaws.com/ (public bucket)",
        "download_script": "src/fetch_oedi.py",
        "selected_system_id": 2107,
        "raw_files_modified": any(not f["unchanged_since_download"] for f in files),
        "files": files,
    }
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    bad = [f["original_filename"] for f in files if not f["unchanged_since_download"]]
    print(f"{len(files)} files; changed since download: {bad or 'none'}")


if __name__ == "__main__":
    main()
