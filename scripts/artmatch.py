#!/usr/bin/env python3
"""Find paintings that look like a given image.

Feed it any image and it searches the ~2,900-work corpus bundled with Muse
(museum catalogs + era paintings — all with public image URLs), ranking by:

  - structure  : Apple Vision feature-print similarity (on-device image
                 embedding — overall composition/content)
  - figures    : detected human figures — count and layout agreement
  - color      : palette histogram + spatial color-grid similarity
  - title      : fuzzy match against --title, when given

Usage:
    python3 Scripts/artmatch.py --build              # one-time corpus index
    python3 Scripts/artmatch.py photo.jpg            # match!
    python3 Scripts/artmatch.py photo.jpg --title "Girl with a Pearl Earring"
    python3 Scripts/artmatch.py photo.jpg --top 8

Outputs a ranked list and saves a side-by-side contact sheet.
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

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("ARTMATCH_HOME", Path.home() / ".artmatch"))
IMAGES = CACHE / "images"
FEATURES_FILE = CACHE / "features.json"
HELPER_SRC = Path(__file__).resolve().parent / "artmatch_vision.swift"
HELPER_BIN = CACHE / "artmatch_vision"
HEADERS = {"User-Agent": "MuseArtMatch/1.0 (help@collectmuse.com)"}


# ── Corpus ────────────────────────────────────────────────────────────────

def corpus():
    """id → work metadata: every harvested corpus file (corpus/*.json), plus
    Muse's bundled catalogs when running inside the Canvas repo."""
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
    missing = {qid: work for qid, work in works.items()
               if not (IMAGES / f"{qid}.jpg").exists()}
    if not missing:
        return
    print(f"downloading {len(missing)} thumbnails…")

    def fetch(item):
        qid, work = item
        target = IMAGES / f"{qid}.jpg"
        # Wikimedia rate-limits bursts — retry with backoff.
        for attempt in range(4):
            try:
                request = urllib.request.Request(thumb_url(work["image"]), headers=HEADERS)
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                Image.open(__import__("io").BytesIO(data)).convert("RGB").save(target, "JPEG", quality=85)
                return None
            except Exception as error:  # noqa: BLE001
                if attempt == 3:
                    return f"{qid}: {error}"
                import time
                time.sleep(4 * (attempt + 1))
        return None

    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = []
        for result in pool.map(fetch, missing.items()):
            done += 1
            if result:
                failures.append(result)
            if done % 250 == 0:
                print(f"  {done}/{len(missing)} ({len(failures)} failed)")
    if failures:
        print(f"  ({len(failures)} failed — skipped; sample: {failures[0]})")


# ── Feature extraction ────────────────────────────────────────────────────

def ensure_helper():
    if HELPER_BIN.exists() and HELPER_BIN.stat().st_mtime >= HELPER_SRC.stat().st_mtime:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    print("compiling Vision helper…")
    subprocess.run(["swiftc", "-O", str(HELPER_SRC), "-o", str(HELPER_BIN)], check=True)


def vision_features(paths):
    """path → {"v": [...], "figures": [...]} via the Vision helper."""
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
        if len(paths) > batch:
            print(f"  vision features {min(start + batch, len(paths))}/{len(paths)}")
    return results


def color_features(path):
    """64-bin RGB histogram + 4×4 spatial color grid."""
    image = Image.open(path).convert("RGB").resize((128, 128))
    histogram = [0] * 64
    pixels = list(image.getdata())
    for r, g, b in pixels:
        histogram[(r // 64) * 16 + (g // 64) * 4 + (b // 64)] += 1
    total = len(pixels)
    histogram = [h / total for h in histogram]
    grid_image = image.resize((4, 4))
    grid = [channel / 255 for pixel in grid_image.getdata() for channel in pixel]
    return {"hist": histogram, "grid": grid}


def build_index():
    works = corpus()
    print(f"corpus: {len(works)} works")
    download_thumbs(works)

    features = json.loads(FEATURES_FILE.read_text()) if FEATURES_FILE.exists() else {}
    todo = [qid for qid in works
            if qid not in features and (IMAGES / f"{qid}.jpg").exists()]
    if todo:
        print(f"extracting features for {len(todo)} works…")
        vision = vision_features([IMAGES / f"{qid}.jpg" for qid in todo])
        for qid in todo:
            key = str(IMAGES / f"{qid}.jpg")
            if key not in vision:
                continue
            entry = vision[key]
            entry.update(color_features(IMAGES / f"{qid}.jpg"))
            features[qid] = entry
        FEATURES_FILE.write_text(json.dumps(features))
    print(f"index ready: {len(features)} works indexed")
    return works, features


# ── Scoring ───────────────────────────────────────────────────────────────

def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0


def color_score(qf, cf):
    intersection = sum(min(a, b) for a, b in zip(qf["hist"], cf["hist"]))
    grid_distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(qf["grid"], cf["grid"]))
                              / len(qf["grid"]))
    return 0.5 * intersection + 0.5 * max(0, 1 - grid_distance * 2)


def figure_score(query_figures, candidate_figures):
    qn, cn = len(query_figures), len(candidate_figures)
    if qn == 0 and cn == 0:
        return 0.6   # both figure-free: mildly compatible, not a strong signal
    if qn == 0 or cn == 0:
        return 0.1
    count_score = min(qn, cn) / max(qn, cn)
    # Greedy centroid matching for layout agreement.
    remaining = [(c[0] + c[2] / 2, c[1] + c[3] / 2) for c in candidate_figures]
    distances = []
    for figure in query_figures:
        qx, qy = figure[0] + figure[2] / 2, figure[1] + figure[3] / 2
        if not remaining:
            break
        best = min(remaining, key=lambda p: (p[0] - qx) ** 2 + (p[1] - qy) ** 2)
        distances.append(math.dist((qx, qy), best))
        remaining.remove(best)
    layout = max(0, 1 - (sum(distances) / len(distances)) * 2) if distances else 0
    return 0.5 * count_score + 0.5 * layout


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
    """Bone → absolute angle, for every bone whose joints were both detected."""
    angles = {}
    for a, b in BONES:
        if a in pose and b in pose:
            angles[(a, b)] = math.atan2(pose[b][1] - pose[a][1],
                                        pose[b][0] - pose[a][0])
    return angles


def pose_pair_score(query_pose, candidate_pose):
    """Mean angular agreement across shared bones — the 'literally the same
    pose' number. None when too few bones overlap to judge."""
    qa, ca = pose_angles(query_pose), pose_angles(candidate_pose)
    shared = set(qa) & set(ca)
    if len(shared) < 3:
        return None
    agreement = [math.cos(qa[bone] - ca[bone]) for bone in shared]
    return max(0, (sum(agreement) / len(agreement) + 1) / 2)


def head_pose_score(query_figures, candidate_figures):
    """Gaze-direction agreement between the primary faces (yaw/roll/pitch)."""
    if not query_figures or not candidate_figures:
        return None
    biggest = lambda figs: max(figs, key=lambda f: f[2] * f[3])  # noqa: E731
    qf, cf = biggest(query_figures), biggest(candidate_figures)
    if len(qf) < 7 or len(cf) < 7:
        return None
    deltas = [abs(qf[i] - cf[i]) for i in (4, 5, 6)]
    return max(0, 1 - sum(deltas) / 3 / 0.9)


def pose_score(query, candidate):
    """The exactness axis: best skeletal match across figure pairs, blended
    with gaze direction. Falls back gracefully when skeletons are missing
    (common in stylized paintings)."""
    body = None
    for query_pose in query.get("poses", []):
        for candidate_pose in candidate.get("poses", []):
            score = pose_pair_score(query_pose, candidate_pose)
            if score is not None and (body is None or score > body):
                body = score
    head = head_pose_score(query.get("figures", []), candidate.get("figures", []))
    if body is not None and head is not None:
        return 0.75 * body + 0.25 * head
    if body is not None:
        return body
    if head is not None:
        return head
    return None


def title_score(query_title, candidate_title):
    a, b = query_title.lower(), candidate_title.lower()
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0
    return max(ratio, jaccard)


def match(query_path, query_title=None, top=5):
    works, features = build_index()

    query = vision_features([Path(query_path)]).get(str(Path(query_path)))
    if not query:
        raise SystemExit("couldn't extract features from the query image")
    query.update(color_features(query_path))

    # A grayscale query (B&W stills, film frames) says nothing about palette —
    # scoring color would just drag the ranking toward dark monochrome works.
    image = Image.open(query_path).convert("RGB").resize((64, 64))
    saturation = sum(max(p) - min(p) for p in image.getdata()) / (64 * 64)
    grayscale = saturation < 12
    if grayscale:
        print("(query is grayscale — ignoring the color axis)")

    weights = ({"visual": 0.30, "pose": 0.30, "color": 0.10, "figures": 0.10, "title": 0.20}
               if query_title else
               {"visual": 0.35, "pose": 0.35, "color": 0.15, "figures": 0.15, "title": 0})
    if grayscale:
        weights["visual"] += weights["color"]
        weights["color"] = 0

    scored = []
    for qid, feats in features.items():
        if qid not in works:
            continue
        pose = pose_score(query, feats)
        parts = {
            "visual": max(0, cosine(query["v"], feats["v"])),
            # No skeleton/face on either side → neutral-low, not zero: stylized
            # works shouldn't be erased, just not rewarded for exactness.
            "pose": pose if pose is not None else 0.3,
            "color": color_score(query, feats),
            "figures": figure_score(query["figures"], feats["figures"]),
            "title": title_score(query_title, works[qid]["title"]) if query_title else 0,
        }
        total = sum(weights[k] * parts[k] for k in weights)
        scored.append((total, parts, qid))
    scored.sort(reverse=True, key=lambda s: s[0])
    return [(total, parts, qid, works[qid]) for total, parts, qid in scored[:top]]


# ── Output ────────────────────────────────────────────────────────────────

def contact_sheet(query_path, matches, out_path):
    tile, gap, caption_height = 340, 12, 58
    count = 1 + len(matches)
    sheet = Image.new("RGB", (count * tile + (count + 1) * gap,
                              tile + caption_height + 2 * gap), (14, 22, 18))
    draw = ImageDraw.Draw(sheet)

    def paste(image_path, index, lines):
        image = Image.open(image_path).convert("RGB")
        image.thumbnail((tile, tile))
        x = gap + index * (tile + gap)
        sheet.paste(image, (x + (tile - image.width) // 2,
                            gap + (tile - image.height) // 2))
        for row, line in enumerate(lines[:3]):
            draw.text((x + 2, tile + gap + 4 + row * 16), line[:44], fill=(232, 226, 210))

    paste(query_path, 0, ["YOUR IMAGE"])
    for index, (total, _, qid, work) in enumerate(matches, start=1):
        paste(IMAGES / f"{qid}.jpg", index,
              [f"#{index}  {work['title']}",
               f"{work['artist']}" + (f", {work['year']}" if work["year"] else ""),
               f"{work['museum']}  ({total:.2f})".strip()])
    sheet.save(out_path, "JPEG", quality=90)


def main():
    args = [a for a in sys.argv[1:]]
    if "--build" in args:
        build_index()
        return
    if not args:
        raise SystemExit(__doc__)
    query_path = args[0]
    query_title = None
    top = 5
    if "--title" in args:
        query_title = args[args.index("--title") + 1]
    if "--top" in args:
        top = int(args[args.index("--top") + 1])

    matches = match(query_path, query_title, top)
    print(f"\ntop {len(matches)} matches:")
    for rank, (total, parts, qid, work) in enumerate(matches, start=1):
        detail = " ".join(f"{k}={v:.2f}" for k, v in parts.items() if v)
        line = f"{rank}. [{total:.3f}] {work['title']} — {work['artist']}"
        if work["year"]:
            line += f" ({work['year']})"
        if work["museum"]:
            line += f" · {work['museum']}"
        print(line)
        print(f"     {detail}  wikidata.org/wiki/{qid}")

    out = Path(tempfile.gettempdir()) / "artmatch_result.jpg"
    contact_sheet(query_path, matches, out)
    print(f"\ncontact sheet: {out}")


if __name__ == "__main__":
    main()
