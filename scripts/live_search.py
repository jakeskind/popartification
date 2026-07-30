#!/usr/bin/env python3
"""Search the live museum APIs — the whole web, not just our harvested index.

The local index is fast but finite; the Zendaya/Spider-Man pairing proved the
cost of that. The judge generated the perfect keywords ("arachne, spider,
web") and found nothing, because no spider artwork had ever been harvested —
and because the harvester filters to paintings, so the Met's *Arachne* etching
could never have been there anyway.

This module queries museum search endpoints at judge time, across ALL object
types (paintings, prints, drawings, sculpture, textiles), fetches thumbnails on
demand, and hands the results to the judge alongside the local candidates.

    from live_search import search_museums
    hits = search_museums(["arachne", "spider", "web"], limit=12)
    # -> [{id,title,artist,year,museum,medium,image,path}, ...]
"""

import io
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

CACHE = Path(__file__).resolve().parent.parent / ".live_cache"
HEADERS = {"User-Agent": "MuseArtMatch/2.0 (help@collectmuse.com)"}
MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/"
CMA_SEARCH = "https://openaccess-api.clevelandart.org/api/artworks/"


def _json(url, attempts=3, timeout=30):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except Exception:  # noqa: BLE001
            if attempt == attempts - 1:
                return None
            time.sleep(4 * (attempt + 1))
    return None


def _met(keyword, per_keyword):
    """Met search across every department and object type (not just
    paintings — prints, drawings and sculpture make some of the best
    matches)."""
    found = []
    data = _json(MET_SEARCH + "?" + urllib.parse.urlencode(
        {"hasImages": "true", "q": keyword}))
    for object_id in (data or {}).get("objectIDs") or []:
        if len(found) >= per_keyword:
            break
        item = _json(MET_OBJECT + str(object_id), attempts=2)
        if not item or not item.get("isPublicDomain"):
            continue
        image = item.get("primaryImageSmall") or item.get("primaryImage")
        if not image:
            continue
        found.append({
            "id": f"met_{object_id}",
            "title": item.get("title") or "Untitled",
            "artist": item.get("artistDisplayName") or "",
            "year": item.get("objectDate") or "",
            "museum": "The Metropolitan Museum of Art",
            "medium": item.get("medium") or "",
            "image": image,
            "hires": item.get("primaryImage") or image,
        })
    return found


def _cleveland(keyword, per_keyword):
    found = []
    data = _json(CMA_SEARCH + "?" + urllib.parse.urlencode(
        {"q": keyword, "has_image": "1", "cc0": "1", "limit": per_keyword * 2,
         "fields": "id,title,creators,creation_date,images,technique"}))
    for item in (data or {}).get("data") or []:
        if len(found) >= per_keyword:
            break
        image = (item.get("images") or {}).get("web", {}).get("url")
        if not image:
            continue
        creators = item.get("creators") or []
        artist = creators[0].get("description", "").split("(")[0].strip() if creators else ""
        found.append({
            "id": f"cma_{item['id']}",
            "title": item.get("title") or "Untitled",
            "artist": artist,
            "year": item.get("creation_date") or "",
            "museum": "Cleveland Museum of Art",
            "medium": item.get("technique") or "",
            "image": image,
            "hires": (item.get("images") or {}).get("print", {}).get("url") or image,
        })
    return found


def search_museums(keywords, limit=12, per_keyword=3):
    """Live search across museum APIs for a set of concept keywords. Returns
    deduped hits with a local thumbnail path ready for the judge."""
    CACHE.mkdir(parents=True, exist_ok=True)
    hits, seen = [], set()

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = []
        for keyword in keywords[:8]:
            jobs.append(pool.submit(_met, keyword, per_keyword))
            jobs.append(pool.submit(_cleveland, keyword, per_keyword))
        for job in jobs:
            try:
                results = job.result()
            except Exception:  # noqa: BLE001
                continue
            for hit in results or []:
                if hit["id"] in seen:
                    continue
                seen.add(hit["id"])
                hits.append(hit)

    def thumb(hit):
        path = CACHE / f"{hit['id']}.jpg"
        if not path.exists():
            try:
                request = urllib.request.Request(hit["image"], headers=HEADERS)
                with urllib.request.urlopen(request, timeout=60) as response:
                    image = Image.open(io.BytesIO(response.read())).convert("RGB")
                image.thumbnail((420, 420))
                image.save(path, "JPEG", quality=85)
            except Exception:  # noqa: BLE001
                return None
        hit["path"] = path
        return hit

    with ThreadPoolExecutor(max_workers=6) as pool:
        hits = [h for h in pool.map(thumb, hits[:limit * 2]) if h]
    return hits[:limit]


if __name__ == "__main__":
    import sys
    words = sys.argv[1:] or ["arachne", "spider", "web"]
    for hit in search_museums(words):
        print(f"{hit['id']}: {hit['title'][:50]} — {hit['artist'][:24]} "
              f"({hit['year'][:12]}) [{hit['medium'][:20]}]")
