#!/usr/bin/env python3
"""Compose a popartification pairing: two images, framed identically.

Detects the dominant face in both the pop-culture image and the artwork, then
crops the ARTWORK so its face fills the same fraction of the frame, at the
same position, as the face in the query — the "zoom until identical" rule.
No banner, no text: just the two halves (credits belong in the caption).

Usage:
    python3 scripts/compose_pairing.py query.jpg art.jpg out.jpg [--stack]

Side-by-side by default; --stack for top/bottom (use when both images are
strongly horizontal).
"""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artmatch import vision_features  # noqa: E402

GAP = 4
CANVAS_W = 1080


def dominant_face(path):
    feats = vision_features([Path(path)], quiet=True).get(str(Path(path)))
    if not feats or not feats["figures"]:
        return None
    f = max(feats["figures"], key=lambda r: r[2] * r[3])
    # Vision: normalized, bottom-left origin → top-left origin.
    return {"x": f[0], "top": 1 - f[1] - f[3], "w": f[2], "h": f[3]}


def face_frame_crop(image, face, aspect, fill=None, rel_center=None):
    """Crop `image` to `aspect` (w/h). When `fill`/`rel_center` are given,
    scale the crop so the face fills that fraction of the crop height and its
    center lands at that relative position; otherwise fit the largest crop
    containing the face."""
    w, h = image.size
    fx = (face["x"] + face["w"] / 2) * w
    fy = (face["top"] + face["h"] / 2) * h
    face_h = face["h"] * h
    face_w = face["w"] * w

    if fill:
        crop_h = face_h / fill
        crop_w = crop_h * aspect
        # Never clip the face horizontally.
        crop_w = max(crop_w, face_w * 1.15)
        crop_h = crop_w / aspect
    else:
        crop_h = h
        crop_w = crop_h * aspect
        if crop_w > w:
            crop_w = w
            crop_h = crop_w / aspect
        crop_w = max(crop_w, min(w, face_w * 1.15))
        crop_h = crop_w / aspect

    crop_w, crop_h = min(crop_w, w), min(crop_h, h)
    rcx, rcy = rel_center or (0.5, 0.45)
    left = min(max(0, fx - rcx * crop_w), w - crop_w)
    top = min(max(0, fy - rcy * crop_h), h - crop_h)
    crop = image.crop((int(left), int(top), int(left + crop_w), int(top + crop_h)))

    achieved = {"fill": face_h / crop_h,
                "rel": ((fx - left) / crop_w, (fy - top) / crop_h)}
    return crop, achieved


def compose(query_path, art_path, out_path, stack=False):
    query = Image.open(query_path).convert("RGB")
    art = Image.open(art_path).convert("RGB")
    query_face = dominant_face(query_path)
    art_face = dominant_face(art_path)
    if not query_face or not art_face:
        raise SystemExit("need a detectable face in both images")

    if stack:
        half_w, half_h = CANVAS_W, 660
    else:
        half_w, half_h = (CANVAS_W - GAP) // 2, 844
    aspect = half_w / half_h

    query_half, achieved = face_frame_crop(query, query_face, aspect)
    # Mirror the query's framing onto the artwork: same face fill, same
    # relative face position.
    art_half, _ = face_frame_crop(art, art_face, aspect,
                                  fill=achieved["fill"], rel_center=achieved["rel"])

    query_half = query_half.resize((half_w, half_h), Image.LANCZOS)
    art_half = art_half.resize((half_w, half_h), Image.LANCZOS)

    if stack:
        canvas = Image.new("RGB", (CANVAS_W, half_h * 2 + GAP), (8, 8, 8))
        canvas.paste(query_half, (0, 0))
        canvas.paste(art_half, (0, half_h + GAP))
    else:
        canvas = Image.new("RGB", (half_w * 2 + GAP, half_h), (8, 8, 8))
        canvas.paste(query_half, (0, 0))
        canvas.paste(art_half, (half_w + GAP, 0))
    canvas.save(out_path, "JPEG", quality=93)
    print(f"saved {out_path} {canvas.size} (face fill {achieved['fill']:.0%})")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--stack"]
    if len(args) < 3:
        raise SystemExit(__doc__)
    compose(args[0], args[1], args[2], stack="--stack" in sys.argv)
