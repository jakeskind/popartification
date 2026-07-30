#!/usr/bin/env python3
"""Compose a pairing from explicit match boxes — WYSIWYG with the preview.

The judge localizes every match (crop boxes + rotation); the user approves an
option by looking at exactly that framing. This composer reproduces it for
the published image, so what was approved is what posts.

Usage:
    python3 scripts/compose_boxes.py query.jpg art.jpg out.jpg \
        --query-box 0.1,0.0,0.8,0.9 --art-box 0.3,0.1,0.5,0.7 [--rotate 90]

Layout follows the house rule: landscape crops stack, portrait crops sit
side by side.
"""

import sys

from PIL import Image

ROTATE_OPS = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}
CANVAS_W, GAP = 1080, 4


def boxed(image, box):
    if not box:
        return image
    w, h = image.size
    left = min(max(box[0], 0.0), 0.95) * w
    top = min(max(box[1], 0.0), 0.95) * h
    width = max(box[2], 0.05) * w
    height = max(box[3], 0.05) * h
    return image.crop((int(left), int(top),
                       int(min(left + width, w)), int(min(top + height, h))))


def fit(image, target_w, target_h):
    """Fill the target box, trimming the long dimension centered — except
    keep the top when trimming height (heads live up there)."""
    aspect = target_w / target_h
    w, h = image.size
    if w / h > aspect:
        crop_w = h * aspect
        left = (w - crop_w) / 2
        image = image.crop((int(left), 0, int(left + crop_w), h))
    else:
        crop_h = w / aspect
        top = min((h - crop_h) * 0.25, h - crop_h)
        image = image.crop((0, int(top), w, int(top + crop_h)))
    return image.resize((target_w, target_h), Image.LANCZOS)


def compose(query_path, art_path, out_path, query_box=None, art_box=None,
            rotate=0):
    query = boxed(Image.open(query_path).convert("RGB"), query_box)
    art = Image.open(art_path).convert("RGB")
    if int(rotate) % 360 in ROTATE_OPS:
        art = art.transpose(ROTATE_OPS[int(rotate) % 360])
    art = boxed(art, art_box)

    mean_aspect = (query.width / query.height + art.width / art.height) / 2
    if mean_aspect > 1.15:   # landscapes stack
        half_w, half_h = CANVAS_W, 660
        canvas = Image.new("RGB", (CANVAS_W, half_h * 2 + GAP), (8, 8, 8))
        canvas.paste(fit(query, half_w, half_h), (0, 0))
        canvas.paste(fit(art, half_w, half_h), (0, half_h + GAP))
    else:                    # portraits side by side
        half_w, half_h = (CANVAS_W - GAP) // 2, 844
        canvas = Image.new("RGB", (half_w * 2 + GAP, half_h), (8, 8, 8))
        canvas.paste(fit(query, half_w, half_h), (0, 0))
        canvas.paste(fit(art, half_w, half_h), (half_w + GAP, 0))
    canvas.save(out_path, "JPEG", quality=93)
    print(f"saved {out_path} {canvas.size} (rotate={rotate})")


def parse_box(text):
    parts = [float(x) for x in text.split(",")]
    return parts if len(parts) == 4 else None


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) < 3:
        raise SystemExit(__doc__)
    query_box = parse_box(argv[argv.index("--query-box") + 1]) \
        if "--query-box" in argv else None
    art_box = parse_box(argv[argv.index("--art-box") + 1]) \
        if "--art-box" in argv else None
    rotate = int(argv[argv.index("--rotate") + 1]) if "--rotate" in argv else 0
    compose(argv[0], argv[1], argv[2], query_box, art_box, rotate)
