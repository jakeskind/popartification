#!/usr/bin/env python3
"""Find the painting (or the *section* of a painting) that mirrors any image.

v2 engine. The index stores, for every artwork:
  - a whole-image Vision fingerprint
  - fingerprints of five region crops (center + quadrants)
  - a fingerprint per detected FACE CROP, with head angles (yaw/roll/pitch)
  - color histograms, face layout, and body-pose skeletons

Face-dominant queries (a close-up like a celebrity still) are matched
face-to-face: crop fingerprints plus head-pose agreement with PITCH weighted
hardest — "head tilted down, looking down" matches only downcast faces. The
winning match can be a cropped detail of a larger canvas, and the contact
sheet shows that crop.

Usage:
    python3 scripts/artmatch.py --build            # (re)index new works
    python3 scripts/artmatch.py photo.jpg [--top 6] [--title "..."]

Cache in ~/.artmatch (override: ARTMATCH_HOME).
"""

import difflib
import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("ARTMATCH_HOME", Path.home() / ".artmatch"))
IMAGES = CACHE / "images"
META_FILE = CACHE / "index_meta.json"
VECS_FILE = CACHE / "index_vecs.f32"
HELPER_SRC = Path(__file__).resolve().parent / "artmatch_vision.swift"
HELPER_BIN = CACHE / "artmatch_vision"
HEADERS = {"User-Agent": "MuseArtMatch/2.0 (help@collectmuse.com)"}

DIM = 768
REGIONS = {  # normalized (left, top, width, height), top-left origin
    "center": (0.25, 0.25, 0.5, 0.5),
    "q1": (0.0, 0.0, 0.55, 0.55),
    "q2": (0.45, 0.0, 0.55, 0.55),
    "q3": (0.0, 0.45, 0.55, 0.55),
    "q4": (0.45, 0.45, 0.55, 0.55),
}


# ── Corpus ────────────────────────────────────────────────────────────────

def corpus():
    works = {}
    era_path = REPO / "Canvas/Resources/EraPaintings.json"
    if era_path.exists():
        for work in json.loads(era_path.read_text()):
            if work.get("image"):
                works[work["qid"]] = {"title": work["title"], "artist": work.get("artist", ""),
                                      "year": work.get("year", ""), "museum": "",
                                      "image": work["image"]}
    catalog_path = REPO / "Canvas/Resources/MuseumCatalogs.json"
    if catalog_path.exists():
        catalogs = json.loads(catalog_path.read_text())
        aliases = json.loads((REPO / "Canvas/Resources/PlaceAliases.json").read_text())
        for museum_qid, entries in catalogs["catalogs"].items():
            museum = aliases["museums"].get(museum_qid, {}).get("name", "")
            for work in entries:
                if work.get("image"):
                    works[work["qid"]] = {"title": work["title"], "artist": work.get("artist", ""),
                                          "year": work.get("year", ""), "museum": museum,
                                          "image": work["image"]}
    corpus_dir = CACHE / "corpus"
    if corpus_dir.exists():
        for source_file in sorted(corpus_dir.glob("*.json")):
            for work in json.loads(source_file.read_text()):
                works[work["id"]] = {"title": work["title"], "artist": work.get("artist", ""),
                                     "year": work.get("year", ""),
                                     "museum": work.get("museum", ""),
                                     "image": work["image"]}
    return works


def thumb_url(image_url, width=360):
    parts = urllib.parse.urlsplit(image_url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["width"] = str(width)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def download_thumbs(works):
    IMAGES.mkdir(parents=True, exist_ok=True)
    missing = {wid: work for wid, work in works.items()
               if not (IMAGES / f"{wid}.jpg").exists()}
    if not missing:
        return
    print(f"downloading {len(missing)} thumbnails…")

    def fetch(item):
        wid, work = item
        for attempt in range(4):
            try:
                url = thumb_url(work["image"]) if "wikimedia" in work["image"] else work["image"]
                request = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                import io
                image = Image.open(io.BytesIO(data)).convert("RGB")
                image.thumbnail((360, 360))
                image.save(IMAGES / f"{wid}.jpg", "JPEG", quality=85)
                return None
            except Exception as error:  # noqa: BLE001
                if attempt == 3:
                    return f"{wid}: {error}"
                import time
                time.sleep(4 * (attempt + 1))
        return None

    done, failures = 0, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch, missing.items()):
            done += 1
            if result:
                failures.append(result)
            if done % 250 == 0:
                print(f"  {done}/{len(missing)} ({len(failures)} failed)")
    if failures:
        print(f"  ({len(failures)} failed — skipped)")


# ── Vision helper ─────────────────────────────────────────────────────────

def ensure_helper():
    if HELPER_BIN.exists() and HELPER_BIN.stat().st_mtime >= HELPER_SRC.stat().st_mtime:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    print("compiling Vision helper…")
    subprocess.run(["swiftc", "-O", str(HELPER_SRC), "-o", str(HELPER_BIN)], check=True)


def vision_features(paths, quiet=False):
    ensure_helper()
    results = {}
    batch = 400
    for start in range(0, len(paths), batch):
        chunk = paths[start:start + batch]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("\n".join(str(p) for p in chunk))
            list_path = handle.name
        out = subprocess.run([str(HELPER_BIN), list_path],
                             capture_output=True, text=True, check=True)
        for line in out.stdout.splitlines():
            row = json.loads(line)
            if "v" in row:
                results[row["path"]] = {"v": row["v"], "figures": row.get("figures", []),
                                        "poses": row.get("poses", [])}
        if not quiet and len(paths) > batch:
            print(f"  vision pass {min(start + batch, len(paths))}/{len(paths)}")
    return results


def color_features(path):
    """Exposure-invariant color signature. A photo of a golden field and a
    painting of one differ wildly in RGB brightness but agree on HUE, so the
    palette is described in HSV: a saturation-weighted hue histogram plus a
    coarse hue/saturation grid. (Naive RGB bins put a bright gold and a dark
    gold in different buckets and scored them as unrelated.)"""
    image = Image.open(path).convert("RGB").resize((128, 128))
    hsv = image.convert("HSV")
    pixels = list(hsv.getdata())

    HUE_BINS = 18
    hue_hist = [0.0] * (HUE_BINS + 1)   # last bin = achromatic
    for h, s, v in pixels:
        if s < 40 or v < 25:
            hue_hist[HUE_BINS] += 1
        else:
            hue_hist[int(h / 256 * HUE_BINS) % HUE_BINS] += s / 255
    total = sum(hue_hist) or 1
    hue_hist = [h / total for h in hue_hist]

    # 4x4 spatial grid of (hue as unit vector, saturation) — brightness left
    # out so exposure can't move it.
    grid_hsv = hsv.resize((4, 4))
    grid = []
    for h, s, v in grid_hsv.getdata():
        angle = h / 256 * 2 * math.pi
        weight = s / 255
        grid += [math.cos(angle) * weight, math.sin(angle) * weight, weight]
    return {"hist": hue_hist, "grid": grid}


def face_crop_box(figure, image_size):
    """Vision face rect (bottom-left origin, normalized) → padded pixel crop
    box (top-left origin)."""
    width, height = image_size
    x, y, w, h = figure[0], figure[1], figure[2], figure[3]
    top = 1 - y - h
    pad = 0.55 * max(w, h)
    left = max(0, (x - pad)) * width
    upper = max(0, (top - pad)) * height
    right = min(1, (x + w + pad)) * width
    lower = min(1, (top + h + pad)) * height
    if right - left < 24 or lower - upper < 24:
        return None
    return (int(left), int(upper), int(right), int(lower))


def crop_norm_rect(box, image_size):
    width, height = image_size
    left, upper, right, lower = box
    return [left / width, upper / height, (right - left) / width, (lower - upper) / height]


# ── Index build ───────────────────────────────────────────────────────────

def load_meta():
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    return {"dim": DIM, "works": {}, "entries": []}


def recolor_index():
    """Recompute color features for every indexed work (cheap, PIL-only) —
    used after the color signature changes."""
    meta = load_meta()
    updated = 0
    for wid in list(meta["works"]):
        path = IMAGES / f"{wid}.jpg"
        if not path.exists():
            continue
        try:
            meta["works"][wid].update(color_features(path))
            updated += 1
        except Exception:  # noqa: BLE001
            continue
        if updated % 2000 == 0:
            print(f"  recolored {updated}")
    META_FILE.write_text(json.dumps(meta))
    print(f"recolored {updated} works")


def build_index():
    works = corpus()
    print(f"corpus: {len(works)} works")
    download_thumbs(works)

    meta = load_meta()
    todo = [wid for wid in works
            if wid not in meta["works"] and (IMAGES / f"{wid}.jpg").exists()]
    if not todo:
        print(f"index up to date: {len(meta['works'])} works, {len(meta['entries'])} vectors")
        return works, meta

    print(f"indexing {len(todo)} new works…")
    base = vision_features([IMAGES / f"{wid}.jpg" for wid in todo])

    crop_dir = Path(tempfile.mkdtemp(prefix="artmatch_crops_"))
    crop_specs = []   # (crop_path, work_id, kind, rect, angles)
    new_entries, new_vectors = [], []

    for wid in todo:
        key = str(IMAGES / f"{wid}.jpg")
        if key not in base:
            continue
        feats = base[key]
        scalars = color_features(IMAGES / f"{wid}.jpg")
        meta["works"][wid] = {"figures": feats["figures"], "poses": feats["poses"],
                              "hist": scalars["hist"], "grid": scalars["grid"]}
        new_entries.append({"work": wid, "kind": "full", "rect": None, "angles": None})
        new_vectors.append(feats["v"])

        image = Image.open(IMAGES / f"{wid}.jpg").convert("RGB")
        for kind, (rx, ry, rw, rh) in REGIONS.items():
            box = (int(rx * image.width), int(ry * image.height),
                   int((rx + rw) * image.width), int((ry + rh) * image.height))
            path = crop_dir / f"{wid}__region_{kind}.jpg"
            image.crop(box).save(path, "JPEG", quality=85)
            crop_specs.append((path, wid, "region", [rx, ry, rw, rh], None))
        for i, figure in enumerate(feats["figures"][:4]):
            box = face_crop_box(figure, image.size)
            if not box:
                continue
            path = crop_dir / f"{wid}__face_{i}.jpg"
            image.crop(box).save(path, "JPEG", quality=85)
            angles = figure[4:7] if len(figure) >= 7 else [0, 0, 0]
            crop_specs.append((path, wid, "face", crop_norm_rect(box, image.size), angles))

    print(f"fingerprinting {len(crop_specs)} crops…")
    crop_features = vision_features([spec[0] for spec in crop_specs])
    for path, wid, kind, rect, angles in crop_specs:
        feats = crop_features.get(str(path))
        if feats:
            new_entries.append({"work": wid, "kind": kind, "rect": rect, "angles": angles})
            new_vectors.append(feats["v"])

    # Append normalized vectors to the binary store.
    array = np.asarray(new_vectors, dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1
    array /= norms
    with open(VECS_FILE, "ab") as handle:
        handle.write(array.tobytes())
    meta["entries"].extend(new_entries)
    META_FILE.write_text(json.dumps(meta))

    import shutil
    shutil.rmtree(crop_dir, ignore_errors=True)
    print(f"index ready: {len(meta['works'])} works, {len(meta['entries'])} vectors")
    return works, meta


# ── Scoring ───────────────────────────────────────────────────────────────

def angle_similarity(query_angles, candidate_angles):
    """Head-pose agreement, pitch loudest: 'looking down' must match looking
    down. Angles are radians (yaw, roll, pitch)."""
    dyaw = abs(query_angles[0] - candidate_angles[0])
    droll = abs(query_angles[1] - candidate_angles[1])
    dpitch = abs(query_angles[2] - candidate_angles[2])
    return (0.50 * max(0, 1 - dpitch / 0.40)
            + 0.30 * max(0, 1 - droll / 0.60)
            + 0.20 * max(0, 1 - dyaw / 0.90))


BONES = [("neck", "nose"), ("neck", "root"),
         ("right_shoulder_1_joint", "right_forearm_joint"),
         ("right_forearm_joint", "right_hand_joint"),
         ("left_shoulder_1_joint", "left_forearm_joint"),
         ("left_forearm_joint", "left_hand_joint"),
         ("right_upLeg_joint", "right_leg_joint"),
         ("right_leg_joint", "right_foot_joint"),
         ("left_upLeg_joint", "left_leg_joint"),
         ("left_leg_joint", "left_foot_joint"),
         ("right_shoulder_1_joint", "left_shoulder_1_joint"),
         ("right_upLeg_joint", "left_upLeg_joint")]


def pose_angles(pose):
    angles = {}
    for a, b in BONES:
        if a in pose and b in pose:
            angles[(a, b)] = math.atan2(pose[b][1] - pose[a][1], pose[b][0] - pose[a][0])
    return angles


def body_pose_score(query_poses, candidate_poses):
    best = None
    for query_pose in query_poses:
        qa = pose_angles(query_pose)
        if len(qa) < 4:
            continue   # face close-ups yield degenerate skeletons — skip
        for candidate_pose in candidate_poses:
            ca = pose_angles(candidate_pose)
            shared = set(qa) & set(ca)
            if len(shared) < 4:
                continue
            agreement = sum(math.cos(qa[b] - ca[b]) for b in shared) / len(shared)
            score = max(0, (agreement + 1) / 2)
            if best is None or score > best:
                best = score
    return best


def color_score(query, work):
    """Palette agreement: hue-histogram intersection plus spatial hue layout.
    Tolerant of legacy indexes built with the old RGB features."""
    if len(query["hist"]) != len(work["hist"]) or len(query["grid"]) != len(work["grid"]):
        return 0.4   # stale entry — neutral rather than misleading
    intersection = sum(min(a, b) for a, b in zip(query["hist"], work["hist"]))
    grid_distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(query["grid"], work["grid"]))
                              / len(query["grid"]))
    return 0.6 * intersection + 0.4 * max(0, 1 - grid_distance * 1.6)


def title_score(query_title, candidate_title):
    a, b = query_title.lower(), candidate_title.lower()
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    return max(ratio, len(ta & tb) / len(ta | tb) if ta | tb else 0)


def keyword_candidates(keywords, limit=40):
    """Works whose TITLE echoes a set of concept keywords — the art-historian
    route ("man swinging a blade in tall grass" → harvest paintings). Visual
    embeddings alone can't find these; titles can."""
    works = corpus()
    wanted = {k.lower().strip() for k in keywords if len(k.strip()) > 2}
    scored = []
    for wid, work in works.items():
        title_words = {w.strip(".,()'\"").lower() for w in work["title"].split()}
        hits = 0
        for k in wanted:
            # Exact word match, or a substantial stem overlap. Short words are
            # matched exactly only — otherwise "a" matches inside "harvest" and
            # every two-word title scores.
            if k in title_words or any(
                    len(w) >= 4 and len(k) >= 4 and (w.startswith(k[:4]) or k.startswith(w[:4]))
                    for w in title_words):
                hits += 1
        if hits:
            # Rank by hit count; shorter titles break ties (more on-the-nose).
            scored.append((hits, -len(title_words), wid, work))
    scored.sort(reverse=True, key=lambda r: (r[0], r[1]))
    return [(wid, work) for _, _, wid, work in scored[:limit]]


def _unused_keyword_tail():
    scored = []
    return [(wid, work) for _, wid, work in scored[:limit]]


def match(query_path, query_title=None, top=6):
    works, meta = build_index()
    vectors = np.fromfile(VECS_FILE, dtype=np.float32).reshape(-1, DIM)
    entries = meta["entries"]

    # Re-encode the query as a clean JPEG first — Vision rejects some PNGs
    # (screenshots with odd color profiles come back "zero-dimensioned").
    clean_path = Path(tempfile.gettempdir()) / "artmatch_query_clean.jpg"
    Image.open(query_path).convert("RGB").save(clean_path, "JPEG", quality=92)
    query_path = str(clean_path)
    query = vision_features([Path(query_path)], quiet=True).get(str(Path(query_path)))
    if not query:
        raise SystemExit("couldn't extract features from the query image")
    query.update(color_features(query_path))
    query_v = np.asarray(query["v"], dtype=np.float32)
    query_v /= (np.linalg.norm(query_v) or 1)

    image = Image.open(query_path).convert("RGB")
    saturation = sum(max(p) - min(p) for p in image.resize((64, 64)).getdata()) / 4096
    grayscale = saturation < 12

    # Dominant face → face-first matching, with a fingerprint of the query's
    # own face crop.
    face_v, face_angles = None, None
    if query["figures"]:
        biggest = max(query["figures"], key=lambda f: f[2] * f[3])
        if biggest[2] * biggest[3] >= 0.04:
            box = face_crop_box(biggest, image.size)
            if box:
                crop_path = Path(tempfile.gettempdir()) / "artmatch_query_face.jpg"
                image.crop(box).save(crop_path, "JPEG", quality=90)
                crop_feats = vision_features([crop_path], quiet=True).get(str(crop_path))
                if crop_feats:
                    face_v = np.asarray(crop_feats["v"], dtype=np.float32)
                    face_v /= (np.linalg.norm(face_v) or 1)
                    face_angles = list(biggest[4:7]) if len(biggest) >= 7 else [0, 0, 0]
    face_mode = face_v is not None
    if face_mode:
        print("(face-dominant query — matching face crops, pitch-weighted)")
    if grayscale:
        print("(query is grayscale — ignoring the color axis)")

    # Transform variants: mirror AND rotations — an abstract goat rotated 90
    # degrees can be the twin (the art side is shown inverse-transformed).
    from PIL import ImageOps
    VARIANTS = [("none", None), ("mirror", None),
                ("rot90", Image.ROTATE_90), ("rot180", Image.ROTATE_180),
                ("rot270", Image.ROTATE_270)]
    variant_vectors = [query_v]
    tmp = Path(tempfile.gettempdir())
    for name, op in VARIANTS[1:]:
        variant_path = tmp / f"artmatch_query_{name}.jpg"
        (ImageOps.mirror(image) if name == "mirror" else image.transpose(op)).save(
            variant_path, "JPEG", quality=90)
        feats = vision_features([variant_path], quiet=True).get(str(variant_path))
        if feats:
            v = np.asarray(feats["v"], dtype=np.float32)
            v /= (np.linalg.norm(v) or 1)
            variant_vectors.append(v)
        else:
            variant_vectors.append(query_v)
    flip_v = variant_vectors[1]

    flip_face_v, flip_face_angles = None, None
    if face_mode:
        flip_face_path = Path(tempfile.gettempdir()) / "artmatch_query_face_flip.jpg"
        ImageOps.mirror(Image.open(
            Path(tempfile.gettempdir()) / "artmatch_query_face.jpg")).save(
            flip_face_path, "JPEG", quality=90)
        flip_feats = vision_features([flip_face_path], quiet=True).get(str(flip_face_path))
        if flip_feats:
            flip_face_v = np.asarray(flip_feats["v"], dtype=np.float32)
            flip_face_v /= (np.linalg.norm(flip_face_v) or 1)
            # Mirroring negates yaw and roll; pitch is unchanged.
            flip_face_angles = [-face_angles[0], -face_angles[1], face_angles[2]]

    sims_stack = np.stack([vectors @ v for v in variant_vectors], axis=1)
    sims_full = sims_stack.max(axis=1)
    best_variant = sims_stack.argmax(axis=1)   # index into VARIANTS
    sims_face = vectors @ face_v if face_mode else None
    if face_mode and flip_face_v is not None:
        sims_face = np.maximum(sims_face, vectors @ flip_face_v)

    best_region = {}   # work → (score, entry_index)
    best_face = {}
    for index, entry in enumerate(entries):
        wid = entry["work"]
        if entry["kind"] in ("full", "region"):
            score = float(sims_full[index])
            if wid not in best_region or score > best_region[wid][0]:
                best_region[wid] = (score, index)
        elif entry["kind"] == "face" and face_mode:
            fingerprint = float(sims_face[index])
            angles = entry.get("angles") or [0, 0, 0]
            gaze = angle_similarity(face_angles, angles)
            if flip_face_angles is not None:
                gaze = max(gaze, angle_similarity(flip_face_angles, angles))
            score = 0.55 * fingerprint + 0.45 * gaze
            if wid not in best_face or score > best_face[wid][0]:
                best_face[wid] = (score, index)

    # In face mode the face axis dominates: whole-image "region" similarity is
    # mostly tonal/background and buries the gaze signal (a B&W query would
    # just retrieve B&W-looking works). Verified on the Ariana test: the pure
    # face axis surfaces downcast Madonnas; the old 50/25 blend surfaced
    # grayscale lockets.
    if face_mode:
        # Face-to-face is the strongest signal for close-ups, but never the
        # ONLY one: whole-image/region shape similarity keeps real weight so an
        # abstract whose forms echo the photo can still win, and faceless works
        # aren't excluded.
        weights = {"face": 0.55, "region": 0.27, "color": 0.10, "figures": 0.08}
    else:
        weights = {"region": 0.38, "body": 0.14, "color": 0.36, "figures": 0.12}
    if grayscale and "color" in weights:
        weights["face" if face_mode else "region"] += weights["color"]
        weights["color"] = 0
    if query_title:
        weights = {k: v * 0.85 for k, v in weights.items()}
        weights["title"] = 0.15

    def figure_layout_score(candidate_figures):
        qn, cn = len(query["figures"]), len(candidate_figures)
        if qn == 0 and cn == 0:
            return 0.6
        if qn == 0 or cn == 0:
            # Faceless candidates (abstracts!) stay in the running — shape and
            # color decide, not a missing-figure penalty.
            return 0.45
        return min(qn, cn) / max(qn, cn)

    scored = []
    for wid, work_meta in meta["works"].items():
        if wid not in works:
            continue
        parts = {}
        region_score, region_index = best_region.get(wid, (0, None))
        parts["region"] = max(0, region_score)
        entry_index = region_index
        if face_mode:
            # No detected face (abstracts, landscapes, sculpture fragments):
            # fall back to the work's own shape similarity rather than a flat
            # penalty, so it competes on form and color.
            fallback = 0.82 * max(0, region_score)
            face_score, face_index = best_face.get(wid, (fallback, None))
            parts["face"] = face_score
            if face_index is not None and face_score >= region_score:
                entry_index = face_index
        else:
            body = body_pose_score(query["poses"], work_meta["poses"])
            parts["body"] = body if body is not None else 0.35
        parts["color"] = color_score(query, work_meta) if not grayscale else 0
        parts["figures"] = figure_layout_score(work_meta["figures"])
        if query_title:
            parts["title"] = title_score(query_title, works[wid]["title"])
        total = sum(weights.get(k, 0) * v for k, v in parts.items())
        scored.append((total, parts, wid, entry_index))

    scored.sort(reverse=True, key=lambda s: s[0])
    results = []
    for total, parts, wid, entry_index in scored[:top]:
        entry = entries[entry_index] if entry_index is not None else None
        transform = "none"
        if entry_index is not None and entries[entry_index]["kind"] in ("full", "region"):
            transform = VARIANTS[best_variant[entry_index]][0]
        results.append((total, parts, wid, works[wid], entry, transform))
    return results


# ── Output ────────────────────────────────────────────────────────────────

INVERSE_TRANSFORM = {"rot90": Image.ROTATE_270, "rot180": Image.ROTATE_180,
                     "rot270": Image.ROTATE_90}


def matched_image(wid, entry, transform="none"):
    """The matched painting — cropped to the winning detail when a crop won,
    inverse-transformed when a rotated/mirrored query variant matched."""
    image = Image.open(IMAGES / f"{wid}.jpg").convert("RGB")
    if entry and entry["kind"] != "full" and entry.get("rect"):
        rx, ry, rw, rh = entry["rect"]
        box = (int(rx * image.width), int(ry * image.height),
               int((rx + rw) * image.width), int((ry + rh) * image.height))
        image = image.crop(box)
    if transform in INVERSE_TRANSFORM:
        image = image.transpose(INVERSE_TRANSFORM[transform])
    elif transform == "mirror":
        from PIL import ImageOps
        image = ImageOps.mirror(image)
    return image, (entry["kind"] if entry else "full")


def contact_sheet(query_path, matches, out_path):
    tile, gap, caption_height = 340, 12, 58
    count = 1 + len(matches)
    sheet = Image.new("RGB", (count * tile + (count + 1) * gap,
                              tile + caption_height + 2 * gap), (14, 22, 18))
    draw = ImageDraw.Draw(sheet)

    def paste(image, index, lines):
        # Scale up as well as down — face-crop details are small.
        scale = min(tile / image.width, tile / image.height)
        image = image.resize((max(1, int(image.width * scale)),
                              max(1, int(image.height * scale))), Image.LANCZOS)
        x = gap + index * (tile + gap)
        sheet.paste(image, (x + (tile - image.width) // 2,
                            gap + (tile - image.height) // 2))
        for row, line in enumerate(lines[:3]):
            draw.text((x + 2, tile + gap + 4 + row * 16), line[:44], fill=(232, 226, 210))

    paste(Image.open(query_path).convert("RGB"), 0, ["YOUR IMAGE"])
    for index, (total, _, wid, work, entry, transform) in enumerate(matches, start=1):
        image, kind = matched_image(wid, entry, transform)
        detail = " · detail" if kind != "full" else ""
        if transform != "none":
            detail += f" · {transform}"
        paste(image, index,
              [f"#{index}  {work['title']}",
               f"{work['artist']}" + (f", {work['year']}" if work["year"] else ""),
               f"{work['museum']}{detail}  ({total:.2f})".strip()])
    sheet.save(out_path, "JPEG", quality=90)


def main():
    args = sys.argv[1:]
    if "--recolor" in args:
        recolor_index()
        return
    if "--build" in args:
        build_index()
        return
    if not args:
        raise SystemExit(__doc__)
    query_path = args[0]
    query_title = args[args.index("--title") + 1] if "--title" in args else None
    top = int(args[args.index("--top") + 1]) if "--top" in args else 6

    matches = match(query_path, query_title, top)
    print(f"\ntop {len(matches)} matches:")
    for rank, (total, parts, wid, work, entry, transform) in enumerate(matches, start=1):
        detail = " ".join(f"{k}={v:.2f}" for k, v in parts.items())
        kind = entry["kind"] if entry else "full"
        line = f"{rank}. [{total:.3f}] {work['title']} — {work['artist']}"
        if work["year"]:
            line += f" ({work['year']})"
        if work["museum"]:
            line += f" · {work['museum']}"
        if kind != "full":
            line += f"  [{kind} crop]"
        if transform != "none":
            line += f"  [{transform}]"
        print(line)
        print(f"     {detail}  ({wid})")

    out = Path(tempfile.gettempdir()) / "artmatch_result.jpg"
    contact_sheet(query_path, matches, out)
    print(f"\ncontact sheet: {out}")


if __name__ == "__main__":
    main()
