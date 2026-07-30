#!/usr/bin/env python3
"""Index the great anthology paintings scene by scene.

Some paintings are not one image but a hundred: Bosch's Garden of Earthly
Delights, Bruegel's Proverbs and Children's Games. Whole-painting matching
can never surface the drummer in the corner of a hellscape — so this script
slices each dense work into overlapping vignette tiles at two scales and
registers every tile as its own corpus work ("… (detail)"). Visual retrieval
then finds micro-scenes directly, and the judge sees them as candidates.

A sidecar (data/anthology_boxes.json) maps each vignette id to its source
image URL and fraction box so the post flow can re-crop from full resolution.

Run once (and again whenever ANTHOLOGY grows):
    python3 scripts/anthology.py          # tile + register + rebuild index
"""

import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artmatch import CACHE, IMAGES, build_index  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
HEADERS = {"User-Agent": "MuseArtMatch/2.0 (help@collectmuse.com)"}
REPO = Path(__file__).resolve().parent.parent
SIDECAR = REPO / "data" / "anthology_boxes.json"

# The canon of paintings-that-are-anthologies.
ANTHOLOGY = [
    {"slug": "bosch_garden", "search": "Garden of Earthly Delights Bosch High Resolution",
     "title": "The Garden of Earthly Delights", "artist": "Hieronymus Bosch",
     "year": "c. 1490–1510", "museum": "Museo del Prado"},
    {"slug": "bruegel_proverbs", "search": "Pieter Brueghel Dutch Proverbs Google Art Project",
     "title": "Netherlandish Proverbs", "artist": "Pieter Bruegel the Elder",
     "year": "1559", "museum": "Gemäldegalerie, Berlin"},
    {"slug": "bruegel_games", "search": "Pieter Bruegel Children's Games Google Art Project",
     "title": "Children's Games", "artist": "Pieter Bruegel the Elder",
     "year": "1560", "museum": "Kunsthistorisches Museum, Vienna"},
    {"slug": "bruegel_death", "search": "Pieter Bruegel Triumph of Death Prado",
     "title": "The Triumph of Death", "artist": "Pieter Bruegel the Elder",
     "year": "c. 1562", "museum": "Museo del Prado"},
    {"slug": "bruegel_carnival", "search": "Bruegel Fight Between Carnival and Lent",
     "title": "The Fight Between Carnival and Lent",
     "artist": "Pieter Bruegel the Elder", "year": "1559",
     "museum": "Kunsthistorisches Museum, Vienna"},
    {"slug": "bruegel_rebels", "search": "Bruegel Fall of the Rebel Angels Brussels",
     "title": "The Fall of the Rebel Angels", "artist": "Pieter Bruegel the Elder",
     "year": "1562", "museum": "Royal Museums of Fine Arts of Belgium"},
    {"slug": "bosch_judgment", "search": "Bosch Last Judgment Vienna triptych",
     "title": "The Last Judgment", "artist": "Hieronymus Bosch",
     "year": "c. 1482", "museum": "Academy of Fine Arts, Vienna"},
    {"slug": "bosch_anthony", "search": "Bosch Temptation of St Anthony Lisbon triptych",
     "title": "The Temptation of Saint Anthony", "artist": "Hieronymus Bosch",
     "year": "c. 1501", "museum": "Museu Nacional de Arte Antiga, Lisbon"},
]

# (window as fraction of width, stride as fraction of window)
SCALES = [(0.30, 0.55), (0.17, 0.60)]
TILE_SIZE = 720
MIN_STD = 17.0   # reject blank sky/wall tiles


def _json_get(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def resolve_file(query):
    """Largest matching file on Commons for a search query."""
    data = _json_get("https://commons.wikimedia.org/w/api.php?" +
                     urllib.parse.urlencode({
                         "action": "query", "list": "search", "format": "json",
                         "srsearch": query, "srnamespace": "6", "srlimit": "5"}))
    best, best_pixels = None, 0
    for result in data.get("query", {}).get("search", []):
        info = _json_get("https://commons.wikimedia.org/w/api.php?" +
                         urllib.parse.urlencode({
                             "action": "query", "titles": result["title"],
                             "prop": "imageinfo", "iiprop": "url|size",
                             "format": "json"}))
        page = list(info["query"]["pages"].values())[0]
        image_info = (page.get("imageinfo") or [{}])[0]
        pixels = image_info.get("width", 0) * image_info.get("height", 0)
        if pixels > best_pixels:
            best, best_pixels = result["title"], pixels
    return best


def fetch_master(file_title, slug):
    """A ~6000px-wide working copy (cached locally)."""
    cache_dir = CACHE / "anthology_src"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{slug}.jpg"
    if cached.exists():
        return Image.open(cached).convert("RGB")
    name = file_title.split(":", 1)[-1].replace(" ", "_")
    url = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
           + urllib.parse.quote(name) + "?width=6000")
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=600) as response:
        data = response.read()
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.save(cached, "JPEG", quality=90)
    return image


def tiles_for(image):
    """Overlapping square windows at each scale, skipping blank ones."""
    W, H = image.size
    for window_frac, stride_frac in SCALES:
        window = int(W * window_frac)
        stride = max(1, int(window * stride_frac))
        for top in range(0, max(1, H - window + 1), stride):
            for left in range(0, max(1, W - window + 1), stride):
                tile = image.crop((left, top, left + window, top + window))
                gray = np.asarray(tile.convert("L").resize((64, 64)),
                                  dtype=np.float32)
                if gray.std() < MIN_STD:
                    continue
                yield ([left / W, top / H, window / W, window / H], tile)


def main():
    entries, sidecar = [], {}
    if SIDECAR.exists():
        sidecar = json.loads(SIDECAR.read_text())

    for work in ANTHOLOGY:
        slug = work["slug"]
        if any(key.startswith(f"vig_{slug}_") for key in sidecar):
            print(f"{slug}: already tiled — skipping")
            continue
        file_title = resolve_file(work["search"])
        if not file_title:
            print(f"{slug}: no Commons file found for {work['search']!r}")
            continue
        print(f"{slug}: {file_title}")
        master = fetch_master(file_title, slug)
        print(f"  master {master.size}")
        count = 0
        source_url = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                      + urllib.parse.quote(
                          file_title.split(":", 1)[-1].replace(" ", "_")))
        for box, tile in tiles_for(master):
            wid = f"vig_{slug}_{count:03d}"
            tile.thumbnail((TILE_SIZE, TILE_SIZE))
            tile.save(IMAGES / f"{wid}.jpg", "JPEG", quality=88)
            entries.append({
                "id": wid, "title": work["title"] + " (detail)",
                "artist": work["artist"], "year": work["year"],
                "museum": work["museum"], "image": source_url + "?width=1080"})
            sidecar[wid] = {"source": source_url, "box": [round(v, 4) for v in box]}
            count += 1
        print(f"  {count} vignettes")

    if entries:
        corpus_file = CACHE / "corpus" / "anthology.json"
        existing = json.loads(corpus_file.read_text()) if corpus_file.exists() else []
        corpus_file.write_text(json.dumps(existing + entries))
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        SIDECAR.write_text(json.dumps(sidecar, indent=1))
        print(f"registered {len(entries)} vignette works; rebuilding index…")
    build_index()


if __name__ == "__main__":
    main()
