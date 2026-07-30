#!/usr/bin/env python3
"""Tile EVERY dense work in the corpus into vignettes — not just the
hand-picked anthology eight.

The index already knows which works are crowded: Vision counted the figures
in each one at index time. Any work with enough figures is an anthology in
miniature — a battle, a procession, a market square — and each of its scenes
should compete as a candidate on its own.

Fetches the work's full-size source image (the stored museum URL, not the
360px thumb), slices overlapping tiles at two scales, registers each
non-blank tile as a shallow corpus work ("… (detail)"), and extends the
sidecar so post-time recropping stays full-resolution.

    python3 scripts/dense_tiles.py [--min-figures 6] [--limit 800]
"""

import io
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artmatch import CACHE, IMAGES, build_index, corpus, load_meta  # noqa: E402

HEADERS = {"User-Agent": "MuseArtMatch/2.0 (help@collectmuse.com)"}
REPO = Path(__file__).resolve().parent.parent
SIDECAR = REPO / "data" / "anthology_boxes.json"

SCALES = [(0.32, 0.60), (0.18, 0.60)]
MIN_STD = 17.0
MIN_SOURCE_WIDTH = 900   # tiles from tiny sources are mush — skip


def tiles_for(image):
    W, H = image.size
    for window_frac, stride_frac in SCALES:
        window = int(W * window_frac)
        if window < 220:
            continue
        stride = max(1, int(window * stride_frac))
        for top in range(0, max(1, H - window + 1), stride):
            for left in range(0, max(1, W - window + 1), stride):
                tile = image.crop((left, top, left + window, top + window))
                gray = np.asarray(tile.convert("L").resize((64, 64)),
                                  dtype=np.float32)
                if gray.std() < MIN_STD:
                    continue
                yield ([left / W, top / H, window / W, window / H], tile)


def process(job):
    wid, work = job
    try:
        request = urllib.request.Request(work["image"], headers=HEADERS)
        with urllib.request.urlopen(request, timeout=120) as response:
            image = Image.open(io.BytesIO(response.read())).convert("RGB")
    except Exception as error:  # noqa: BLE001
        return wid, None, f"fetch failed: {error}"
    if image.width < MIN_SOURCE_WIDTH:
        return wid, None, f"source only {image.width}px wide"
    results = []
    for index, (box, tile) in enumerate(tiles_for(image)):
        tile.thumbnail((640, 640))
        results.append((f"vig_{wid}_{index:03d}", box, tile))
    return wid, results, None


def main():
    args = sys.argv[1:]
    min_figures = int(args[args.index("--min-figures") + 1]) \
        if "--min-figures" in args else 6
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 800

    meta = load_meta()
    works = corpus()
    sidecar = json.loads(SIDECAR.read_text()) if SIDECAR.exists() else {}

    dense = []
    for wid, stored in meta["works"].items():
        if wid.startswith("vig_") or wid not in works:
            continue
        if any(key.startswith(f"vig_{wid}_") for key in sidecar):
            continue
        figures = stored.get("figures") or []
        if len(figures) >= min_figures:
            dense.append((len(figures), wid))
    dense.sort(reverse=True)
    dense = dense[:limit]
    print(f"{len(dense)} dense works to tile (≥{min_figures} figures)")

    entries, tiled, skipped = [], 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [(wid, works[wid]) for _, wid in dense]
        for wid, results, error in pool.map(process, jobs):
            if error or not results:
                skipped += 1
                continue
            work = works[wid]
            for tile_id, box, tile in results:
                tile.save(IMAGES / f"{tile_id}.jpg", "JPEG", quality=87)
                entries.append({
                    "id": tile_id,
                    "title": work["title"][:160] + " (detail)",
                    "artist": work["artist"], "year": work["year"],
                    "museum": work["museum"], "image": work["image"],
                    "shallow": True,
                })
                sidecar[tile_id] = {"source": work["image"],
                                    "box": [round(v, 4) for v in box]}
            tiled += 1
            if tiled % 50 == 0:
                print(f"  tiled {tiled}/{len(dense)} works "
                      f"({len(entries)} vignettes)")

    print(f"tiled {tiled} works, skipped {skipped}; {len(entries)} vignettes")
    if entries:
        corpus_file = CACHE / "corpus" / "dense_vignettes.json"
        existing = json.loads(corpus_file.read_text()) \
            if corpus_file.exists() else []
        corpus_file.write_text(json.dumps(existing + entries))
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        SIDECAR.write_text(json.dumps(sidecar, indent=1))
    build_index()


if __name__ == "__main__":
    main()
