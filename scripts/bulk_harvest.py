#!/usr/bin/env python3
"""Bulk-harvest open-access collections toward a 100k-work corpus.

Sources (all bulk/paginated — no per-object API hammering):
  - Art Institute of Chicago: the official S3 data dump (~120MB), filtered to
    public-domain works with images → IIIF URLs. ~50k works.
  - Cleveland Museum of Art: full CC0 catalog, paginated 1000 at a time. ~31k.
  - SMK (National Gallery of Denmark): full public-domain set, paginated. ~20k+.

Every bulk work registers with shallow=true: the indexer computes one
full-image vector (plus figure/color scalars) and skips the per-work region
and face crops — that keeps 100k works indexable in hours, not days. The
original deep-indexed 10k works and the vignette tiles keep their full
treatment and remain the precision layer.

Resumable: each source writes its corpus file only when done; re-running
skips sources whose files exist. Then build_index() picks up everything.

    python3 scripts/bulk_harvest.py
"""

import bz2
import io
import json
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artmatch import CACHE, build_index  # noqa: E402

HEADERS = {"User-Agent": "MuseArtMatch/2.0 (help@collectmuse.com)"}
CORPUS = CACHE / "corpus"

AIC_DUMP = "https://artic-api-data.s3.amazonaws.com/artic-api-data.tar.bz2"
CMA_API = "https://openaccess-api.clevelandart.org/api/artworks/"
SMK_API = "https://api.smk.dk/api/v1/art/search"


def _json_get(url, attempts=4, timeout=120):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except Exception as error:  # noqa: BLE001
            if attempt == attempts - 1:
                raise
            print(f"  retry ({error})")
            time.sleep(8 * (attempt + 1))


def harvest_aic():
    """The whole museum in one tarball — no rate limits to fight."""
    out = CORPUS / "aic_bulk.json"
    if out.exists():
        print("aic: already harvested")
        return
    dump = CACHE / "artic-api-data.tar.bz2"
    if not dump.exists():
        print("aic: downloading data dump…")
        request = urllib.request.Request(AIC_DUMP, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=1800) as response, \
                open(dump, "wb") as f:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        print(f"aic: dump {dump.stat().st_size // (1 << 20)} MB")

    works, scanned = [], 0
    with tarfile.open(fileobj=bz2.open(dump), mode="r|") as tar:
        for member in tar:
            if "artworks/" not in member.name or not member.name.endswith(".json"):
                continue
            scanned += 1
            try:
                data = json.load(tar.extractfile(member))
            except Exception:  # noqa: BLE001
                continue
            if not data.get("is_public_domain") or not data.get("image_id"):
                continue
            base = f"https://www.artic.edu/iiif/2/{data['image_id']}/full"
            works.append({
                "id": f"aicb_{data['id']}",
                "title": (data.get("title") or "Untitled")[:200],
                "artist": (data.get("artist_title") or "")[:100],
                "year": (data.get("date_display") or "")[:40],
                "museum": "Art Institute of Chicago",
                "image": base + "/360,/0/default.jpg",
                "shallow": True,
            })
            if scanned % 20000 == 0:
                print(f"  aic: scanned {scanned}, kept {len(works)}")
    out.write_text(json.dumps(works))
    print(f"aic: {len(works)} public-domain works")


def harvest_cleveland():
    out = CORPUS / "cleveland_full.json"
    if out.exists():
        print("cleveland: already harvested")
        return
    works, skip = [], 0
    while True:
        data = _json_get(CMA_API + "?" + urllib.parse.urlencode(
            {"cc0": "1", "has_image": "1", "limit": 1000, "skip": skip,
             "fields": "id,title,creators,creation_date,images,type"}))
        batch = data.get("data") or []
        if not batch:
            break
        for item in batch:
            image = (item.get("images") or {}).get("web", {}).get("url")
            if not image:
                continue
            creators = item.get("creators") or []
            artist = creators[0].get("description", "").split("(")[0].strip() \
                if creators else ""
            works.append({
                "id": f"cmab_{item['id']}",
                "title": (item.get("title") or "Untitled")[:200],
                "artist": artist[:100],
                "year": (item.get("creation_date") or "")[:40],
                "museum": "Cleveland Museum of Art",
                "image": image,
                "shallow": True,
            })
        skip += 1000
        print(f"  cleveland: {len(works)} so far")
        time.sleep(1)
    out.write_text(json.dumps(works))
    print(f"cleveland: {len(works)} works")


def harvest_smk():
    out = CORPUS / "smk.json"
    if out.exists():
        print("smk: already harvested")
        return
    works, offset = [], 0
    while True:
        data = _json_get(SMK_API + "?" + urllib.parse.urlencode(
            {"keys": "*", "filters": "[has_image:true],[public_domain:true]",
             "rows": 2000, "offset": offset}))
        batch = data.get("items") or []
        if not batch:
            break
        for item in batch:
            thumb = item.get("image_thumbnail")
            if not thumb:
                continue
            titles = item.get("titles") or [{}]
            production = (item.get("production") or [{}])[0]
            date = (item.get("production_date") or [{}])[0]
            works.append({
                "id": f"smk_{item.get('object_number', '')}",
                "title": (titles[0].get("title") or "Untitled")[:200],
                "artist": (production.get("creator") or "")[:100],
                "year": (date.get("period") or "")[:40],
                "museum": "SMK — National Gallery of Denmark",
                "image": thumb,
                "shallow": True,
            })
        offset += 2000
        print(f"  smk: {len(works)} so far")
        if offset >= data.get("found", 0):
            break
        time.sleep(1)
    out.write_text(json.dumps(works))
    print(f"smk: {len(works)} works")


def main():
    CORPUS.mkdir(parents=True, exist_ok=True)
    for harvest in (harvest_cleveland, harvest_smk, harvest_aic):
        try:
            harvest()
        except Exception as error:  # noqa: BLE001
            print(f"{harvest.__name__} failed: {error} — continuing")
    build_index()


if __name__ == "__main__":
    main()
