#!/usr/bin/env python3
"""Search the live museum APIs — the whole web, not just our harvested index.

Sources (all keyless, all queried in parallel): The Met, Cleveland Museum of
Art, Wikimedia Commons (the widest net — effectively every public-domain
artwork), Victoria and Albert Museum, the Art Institute of Chicago, and
Wikidata (structured "depicts" queries — the only source that searches the
SUBJECT of a work rather than its text).

Keyed sources worth adding if the user supplies keys: Smithsonian Open Access
(api.si.edu), Harvard Art Museums, Rijksmuseum, Europeana.

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
import re
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




# ── Wikimedia Commons ─────────────────────────────────────────────────────
# The widest net by far: effectively every public-domain artwork, no key. We
# search the File namespace and pull artist/date out of the file's metadata.

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ART_HINTS = ("painting", "portrait", "etching", "engraving", "drawing", "oil",
             "canvas", "fresco", "sculpture", "lithograph", "watercolor",
             "watercolour", "woodcut", "tapestry", "mural")
# Commons is full of scanned books and documents that mention art; these are
# never usable as a pairing image.
ART_EXCLUDE = ("catalog", "catalogue", "page", "index", "entries", "book",
               "text", "letter", "manuscript", "map", "plan", "diagram",
               "titlepage", "title page", "logo", "coat of arms", "stamp",
               "banknote", "chart", "poster for", "sheet music", "photograph of")


def _commons(keyword, per_keyword):
    """Artwork files on Commons matching a keyword."""
    found = []
    data = _json(COMMONS_API + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "format": "json",
        "srsearch": f"{keyword} painting|etching|drawing|sculpture",
        "srnamespace": "6", "srlimit": str(per_keyword * 4),
    }))
    titles = [r["title"] for r in (data or {}).get("query", {}).get("search", [])]
    if not titles:
        return found
    meta = _json(COMMONS_API + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": "600",
        "titles": "|".join(titles[:20]),
    }))
    for page in (meta or {}).get("query", {}).get("pages", {}).values():
        if len(found) >= per_keyword:
            break
        info = (page.get("imageinfo") or [{}])[0]
        extra = info.get("extmetadata") or {}
        name = page.get("title", "File:").split(":", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        # Keep artwork reproductions; drop scanned books and documents.
        lowered = stem.lower()
        if any(bad in lowered for bad in ART_EXCLUDE):
            continue
        # Either an explicit medium word, or the "Artist - Title" convention
        # Commons uses for artwork scans.
        looks_like_art = (any(hint in lowered for hint in ART_HINTS)
                          or re.search(r"^[A-Z][^-]{3,40}\s-\s\S", stem))
        if not looks_like_art:
            continue
        if not info.get("thumburl"):
            continue

        def field(key):
            value = (extra.get(key) or {}).get("value", "")
            return re.sub(r"<[^>]+>", "", value).strip()[:90]

        artist = field("Artist")
        title = field("ObjectName") or stem.replace("_", " ")
        found.append({
            "id": "commons_" + re.sub(r"[^A-Za-z0-9]+", "_", stem)[:60],
            "title": title,
            "artist": artist,
            "year": field("DateTimeOriginal")[:24],
            "museum": field("Credit") or "Wikimedia Commons",
            "medium": field("Medium"),
            "image": info["thumburl"],
            "hires": info.get("url") or info["thumburl"],
        })
    return found


# ── Victoria and Albert Museum ────────────────────────────────────────────

VAM_SEARCH = "https://api.vam.ac.uk/v2/objects/search"


def _vam(keyword, per_keyword):
    found = []
    data = _json(VAM_SEARCH + "?" + urllib.parse.urlencode({
        "q": keyword, "images_exist": "1", "page_size": per_keyword * 2,
    }))
    for record in (data or {}).get("records") or []:
        if len(found) >= per_keyword:
            break
        image_id = record.get("_primaryImageId")
        if not image_id:
            continue
        base = f"https://framemark.vam.ac.uk/collections/{image_id}/"
        found.append({
            "id": f"vam_{record.get('systemNumber', image_id)}",
            "title": record.get("_primaryTitle") or "Untitled",
            "artist": (record.get("_primaryMaker") or {}).get("name", ""),
            "year": record.get("_primaryDate") or "",
            "museum": "Victoria and Albert Museum",
            "medium": record.get("objectType") or "",
            "image": base + "full/600,/0/default.jpg",
            "hires": base + "full/1400,/0/default.jpg",
        })
    return found


# ── Art Institute of Chicago (single query per keyword — no bulk paging) ──

AIC_SEARCH = "https://api.artic.edu/api/v1/artworks/search"


def _aic(keyword, per_keyword):
    found = []
    data = _json(AIC_SEARCH + "?" + urllib.parse.urlencode({
        "q": keyword, "limit": per_keyword * 3,
        "fields": "id,title,artist_title,date_display,image_id,medium_display,is_public_domain",
    }))
    for item in (data or {}).get("data") or []:
        if len(found) >= per_keyword:
            break
        if not item.get("image_id") or not item.get("is_public_domain"):
            continue
        base = f"https://www.artic.edu/iiif/2/{item['image_id']}/full/"
        found.append({
            "id": f"aic_{item['id']}",
            "title": item.get("title") or "Untitled",
            "artist": item.get("artist_title") or "",
            "year": item.get("date_display") or "",
            "museum": "Art Institute of Chicago",
            "medium": item.get("medium_display") or "",
            "image": base + "600,/0/default.jpg",
            "hires": base + "1686,/0/default.jpg",
        })
    return found


# ── Wikidata (structured "depicts") ───────────────────────────────────────
# The only source that can answer "artworks that DEPICT a spider" as a fact
# rather than a text match — it searches the subject, not the caption.

WD_SPARQL = "https://query.wikidata.org/sparql"


def _wikidata(keyword, per_keyword):
    found = []
    # Resolve the keyword to a Wikidata entity, then find artworks depicting it.
    search = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "wbsearchentities", "search": keyword, "language": "en",
        "format": "json", "limit": "1",
    }))
    results = (search or {}).get("search") or []
    if not results:
        return found
    qid = results[0]["id"]
    sparql = f"""
    SELECT ?w ?wLabel ?creatorLabel ?image ?inception WHERE {{
      ?w wdt:P180 wd:{qid} ; wdt:P18 ?image .
      OPTIONAL {{ ?w wdt:P170 ?creator. }}
      OPTIONAL {{ ?w wdt:P571 ?inception. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }} LIMIT {per_keyword * 2}
    """
    data = _json(WD_SPARQL + "?" + urllib.parse.urlencode(
        {"query": sparql, "format": "json"}), attempts=2, timeout=45)
    for row in (data or {}).get("results", {}).get("bindings", []):
        if len(found) >= per_keyword:
            break
        image = row.get("image", {}).get("value")
        if not image:
            continue
        thumb = image.replace("http://", "https://")
        thumb += ("&" if "?" in thumb else "?") + "width=600"
        wqid = row["w"]["value"].rsplit("/", 1)[-1]
        year = row.get("inception", {}).get("value", "")[:4]
        found.append({
            "id": f"wd_{wqid}",
            "title": row.get("wLabel", {}).get("value", "Untitled"),
            "artist": row.get("creatorLabel", {}).get("value", ""),
            "year": year,
            "museum": "Wikidata / Wikimedia",
            "medium": "",
            "image": thumb,
            "hires": image.replace("http://", "https://"),
        })
    return found


SOURCES = (_met, _cleveland, _commons, _vam, _aic, _wikidata)


def search_museums(keywords, limit=12, per_keyword=3):
    """Live search across museum APIs for a set of concept keywords. Returns
    deduped hits with a local thumbnail path ready for the judge."""
    CACHE.mkdir(parents=True, exist_ok=True)
    hits, seen = [], set()

    # Every source x every keyword, all in flight at once.
    with ThreadPoolExecutor(max_workers=12) as pool:
        jobs = [pool.submit(source, keyword, per_keyword)
                for keyword in keywords[:8] for source in SOURCES]
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

    with ThreadPoolExecutor(max_workers=10) as pool:
        hits = [h for h in pool.map(thumb, hits[:limit * 3]) if h]
    return hits[:limit]


if __name__ == "__main__":
    import sys
    words = sys.argv[1:] or ["arachne", "spider", "web"]
    for hit in search_museums(words):
        print(f"{hit['id']}: {hit['title'][:50]} — {hit['artist'][:24]} "
              f"({hit['year'][:12]}) [{hit['medium'][:20]}]")
