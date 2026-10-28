"""Download public PVDAQ objects from the OEDI S3 bucket over HTTPS, unchanged.

Each object is saved under data/raw/<S3 key> and a provenance record
(URL, access time, size, SHA-256, ETag, Last-Modified) is appended to
data/raw/download_log.jsonl. Existing files are not overwritten.

Usage:
    python src/fetch_oedi.py list <prefix>
    python src/fetch_oedi.py get <key> [<key> ...]
"""
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

BUCKET_URL = "https://oedi-data-lake.s3.amazonaws.com/"
S3_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
LOG_PATH = RAW_DIR / "download_log.jsonl"


def list_prefix(prefix):
    """Return (sub-prefixes, [(key, size, last_modified)]) for one S3 prefix level."""
    dirs, files, token = [], [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "delimiter": "/"}
        if token:
            params["continuation-token"] = token
        resp = requests.get(BUCKET_URL, params=params, timeout=60)
        resp.raise_for_status()
        tree = ET.fromstring(resp.content)
        dirs += [p.text for p in tree.findall("s:CommonPrefixes/s:Prefix", S3_NS)]
        for c in tree.findall("s:Contents", S3_NS):
            files.append((c.find("s:Key", S3_NS).text,
                          int(c.find("s:Size", S3_NS).text),
                          c.find("s:LastModified", S3_NS).text))
        if tree.find("s:IsTruncated", S3_NS).text != "true":
            return dirs, files
        token = tree.find("s:NextContinuationToken", S3_NS).text


def download(key):
    url = BUCKET_URL + key
    dest = RAW_DIR / key
    if dest.exists():
        print(f"exists, skipped: {dest.relative_to(ROOT)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    accessed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sha = hashlib.sha256()
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
                sha.update(chunk)
        expected = int(resp.headers.get("Content-Length", -1))
        size = tmp.stat().st_size
        status = "complete" if expected in (-1, size) else "size_mismatch"
        tmp.rename(dest)
        record = {
            "key": key,
            "url": url,
            "local_path": dest.relative_to(ROOT).as_posix(),
            "accessed_utc": accessed,
            "size_bytes": size,
            "content_length_header": expected,
            "sha256": sha.hexdigest(),
            "etag": resp.headers.get("ETag", "").strip('"'),
            "last_modified": resp.headers.get("Last-Modified"),
            "status": status,
        }
    with open(LOG_PATH, "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    print(f"{status}: {record['local_path']} ({size} bytes)")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "list":
        dirs, files = list_prefix(args[0])
        for d in dirs:
            print("DIR ", d)
        for key, size, modified in files:
            print("FILE", key, size, modified)
    elif cmd == "get":
        for k in args:
            try:
                download(k)
            except requests.HTTPError as err:
                print(f"failed: {k} ({err.response.status_code})")
