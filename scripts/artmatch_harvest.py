#!/usr/bin/env python3
"""Harvest large open-access painting corpora for the art matcher.

Pulls public-domain painting metadata + thumbnails from museum open APIs
(no keys needed) into the shared ~/.artmatch cache:

    ~/.artmatch/corpus/<source>.json   [{id,title,artist,year,museum,image}]
    ~/.artmatch/images/<id>.jpg        360px thumbnails

Sources:
    aic        Art Institute of Chicago (~20K public-domain paintings)
    cleveland  Cleveland Museum of Art (~7K CC0 paintings)
    met        The Met Open Access (~25K public-domain paintings; slowest —
               needs one API call per object for the image URL)

Usage:
    python3 Scripts/artmatch_harvest.py aic cleveland      # fast pair first
    python3 Scripts/artmatch_harvest.py met                # overnight-ish
"""

import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

CACHE = Path(os.environ.get("ARTMATCH_HOME", Path.home() / ".artmatch"))
CORPUS = CACHE / "corpus"
IMAGES = CACHE / "images"
HEADERS = {"User-Agent": "MuseArtMatch/1.0 (help@collectmuse.com)"}


def get_json(url, retries=4, payload=None):
    for attempt in range(retries):
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            headers = dict(HEADERS)
            if payload is not None:
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            # Museum APIs rate-limit anonymous bursts — back off generously.
            time.sleep(12 * (attempt + 1))


def save_thumb(work):
    target = IMAGES / f"{work['id']}.jpg"
    if target.exists():
        return None
    for attempt in range(3):
        try:
            request = urllib.request.Request(work["image"], headers=HEADERS)
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            image = Image.open(io.BytesIO(data)).convert("RGB")
            image.thumbnail((360, 360))
            image.save(target, "JPEG", quality=85)
            return None
        except Exception as error:  # noqa: BLE001
            if attempt == 2:
                return f"{work['id']}: {error}"
            time.sleep(3 * (attempt + 1))
    return None


def download_all(works, workers):
    failures = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(save_thumb, works):
            done += 1
            if result:
                failures.append(result)
            if done % 500 == 0:
                print(f"  thumbs {done}/{len(works)} ({len(failures)} failed)")
    print(f"  thumbs done: {done - len(failures)}/{len(works)}")


def write_corpus(source, works):
    CORPUS.mkdir(parents=True, exist_ok=True)
    (CORPUS / f"{source}.json").write_text(json.dumps(works))
    print(f"{source}: {len(works)} works catalogued")


# ── Art Institute of Chicago ──────────────────────────────────────────────

def harvest_aic():
    print("harvesting Art Institute of Chicago…")
    works, page = [], 1
    while True:
        data = get_json("https://api.artic.edu/api/v1/artworks/search", payload={
            "query": {"bool": {"must": [
                {"term": {"is_public_domain": True}},
                {"term": {"artwork_type_id": 1}},   # paintings
            ]}},
            "fields": ["id", "title", "artist_title", "date_display", "image_id"],
            "limit": 100, "page": page,
        })
        for item in data.get("data", []):
            if item.get("image_id"):
                works.append({
                    "id": f"aic_{item['id']}",
                    "title": item.get("title") or "Untitled",
                    "artist": item.get("artist_title") or "",
                    "year": item.get("date_display") or "",
                    "museum": "Art Institute of Chicago",
                    "image": f"https://www.artic.edu/iiif/2/{item['image_id']}/full/360,/0/default.jpg",
                })
        total_pages = data.get("pagination", {}).get("total_pages", page)
        if page % 5 == 0:
            print(f"  metadata page {page}/{total_pages}")
        if page >= total_pages:
            break
        page += 1
        time.sleep(1.5)
    write_corpus("aic", works)
    download_all(works, workers=8)


# ── Cleveland Museum of Art ───────────────────────────────────────────────

def harvest_cleveland():
    print("harvesting Cleveland Museum of Art…")
    works, skip = [], 0
    while True:
        url = ("https://openaccess-api.clevelandart.org/api/artworks/?"
               + urllib.parse.urlencode({
                   "type": "Painting", "has_image": "1", "cc0": "1",
                   "limit": "500", "skip": str(skip),
                   "fields": "id,title,creators,creation_date,images",
               }))
        data = get_json(url)
        rows = data.get("data", [])
        if not rows:
            break
        for item in rows:
            image = (item.get("images") or {}).get("web", {}).get("url")
            if image:
                creators = item.get("creators") or []
                artist = creators[0].get("description", "").split("(")[0].strip() if creators else ""
                works.append({
                    "id": f"cma_{item['id']}",
                    "title": item.get("title") or "Untitled",
                    "artist": artist,
                    "year": item.get("creation_date") or "",
                    "museum": "Cleveland Museum of Art",
                    "image": image,
                })
        skip += 500
        print(f"  metadata {len(works)} works…")
        time.sleep(0.3)
    write_corpus("cleveland", works)
    download_all(works, workers=8)


# ── The Met ───────────────────────────────────────────────────────────────

def harvest_met():
    print("harvesting The Met (this is the slow one — one call per object)…")
    search = get_json("https://collectionapi.metmuseum.org/public/collection/v1/search?"
                      + urllib.parse.urlencode({
                          "hasImages": "true", "medium": "Paintings", "q": "*",
                      }))
    ids = search.get("objectIDs") or []
    print(f"  {len(ids)} candidate objects")

    existing = {}
    partial = CORPUS / "met.json"
    if partial.exists():
        existing = {w["id"]: w for w in json.loads(partial.read_text())}
        print(f"  resuming — {len(existing)} already catalogued")

    works = list(existing.values())
    todo = [i for i in ids if f"met_{i}" not in existing]

    def fetch_object(object_id):
        try:
            item = get_json("https://collectionapi.metmuseum.org/public/collection/v1/objects/"
                            + str(object_id), retries=2)
        except Exception:  # noqa: BLE001
            return None
        if not item.get("isPublicDomain") or not item.get("primaryImageSmall"):
            return None
        return {
            "id": f"met_{object_id}",
            "title": item.get("title") or "Untitled",
            "artist": item.get("artistDisplayName") or "",
            "year": item.get("objectDate") or "",
            "museum": "The Metropolitan Museum of Art",
            "image": item["primaryImageSmall"],
        }

    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(fetch_object, todo):
            done += 1
            if result:
                works.append(result)
            if done % 1000 == 0:
                print(f"  metadata {done}/{len(todo)} ({len(works)} kept)")
                write_corpus("met", works)   # checkpoint for resume
    write_corpus("met", works)
    download_all(works, workers=8)


HARVESTERS = {"aic": harvest_aic, "cleveland": harvest_cleveland, "met": harvest_met}


def main():
    sources = [a for a in sys.argv[1:] if a in HARVESTERS]
    if not sources:
        raise SystemExit(__doc__)
    IMAGES.mkdir(parents=True, exist_ok=True)
    for source in sources:
        try:
            HARVESTERS[source]()
        except Exception as error:  # noqa: BLE001 - a flaky API shouldn't kill the rest
            print(f"!! {source} harvest failed: {error}")


if __name__ == "__main__":
    main()
