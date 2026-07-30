#!/usr/bin/env python3
"""The pairing score: how literally identical are these two images?

Computes a style-blind visual-twin score over every zoom/rotation combination:
the artwork is tried whole, center-zoomed, and quadrant-cropped, under all
five transforms (identity, mirror, rot90/180/270), against the photo whole
and center-zoomed — the best combination wins and is reported, so the
composer can reproduce it ("Goat (rotated)" is a legitimate match).

Style-blind by construction: the features are structure (edge-energy grid),
palette (saturation-weighted hue histogram), and light (luminance grid) —
none of which require realism, so an Arthur Dove abstract competes with a
Rembrandt on equal terms.

Pure PIL + numpy: runs identically on a Mac with the full index and on a
bare cloud runner.

    from score import twin_score
    twin = twin_score(photo_pil, artwork_pil)
    # {"score": 7.4, "transform": "rot90", "art_crop": "q1", "query_crop": "full"}
"""

import numpy as np
from PIL import Image

# (name, (left, top, width, height) as fractions)
ART_CROPS = [
    ("full", (0.0, 0.0, 1.0, 1.0)),
    ("center", (0.15, 0.15, 0.70, 0.70)),
    ("upper", (0.10, 0.00, 0.80, 0.60)),
    ("q1", (0.0, 0.0, 0.55, 0.55)), ("q2", (0.45, 0.0, 0.55, 0.55)),
    ("q3", (0.0, 0.45, 0.55, 0.55)), ("q4", (0.45, 0.45, 0.55, 0.55)),
]
QUERY_CROPS = [
    ("full", (0.0, 0.0, 1.0, 1.0)),
    ("center", (0.12, 0.12, 0.76, 0.76)),
]
TRANSFORMS = [
    ("none", None), ("mirror", Image.FLIP_LEFT_RIGHT),
    ("rot90", Image.ROTATE_90), ("rot180", Image.ROTATE_180),
    ("rot270", Image.ROTATE_270),
]


def _crop(image, box):
    w, h = image.size
    left, top, cw, ch = box
    return image.crop((int(left * w), int(top * h),
                       int((left + cw) * w), int((top + ch) * h)))


def _features(image):
    """Structure + palette + light, each L2-normalized."""
    array = np.asarray(image.convert("RGB").resize((96, 96)),
                       dtype=np.float32) / 255.0
    lum = array @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    gy, gx = np.gradient(lum)
    energy = np.hypot(gx, gy)
    # Mini-HOG: edge ORIENTATION histograms per cell. Orientation is what
    # makes structure directional — a diagonal sword reads differently from a
    # vertical mast — and what makes rotations meaningful rather than free.
    bins, cells, cell = 6, 6, 16
    angle = (np.arctan2(gy, gx) % np.pi) / np.pi * bins
    bin_index = np.clip(angle.astype(np.int32), 0, bins - 1)
    ys, xs = np.mgrid[0:96, 0:96]
    cell_index = (ys // cell) * cells + (xs // cell)
    hog = np.zeros((cells * cells, bins), dtype=np.float32)
    np.add.at(hog, (cell_index.ravel(), bin_index.ravel()), energy.ravel())
    structure = hog.ravel()
    light = lum.reshape(8, 12, 8, 12).mean(axis=(1, 3)).ravel()

    # Vectorized hue, weighted by saturation*value so gray pixels don't vote.
    mx, mn = array.max(2), array.min(2)
    diff = mx - mn + 1e-6
    r, g, b = array[..., 0], array[..., 1], array[..., 2]
    hue = np.where(mx == r, (g - b) / diff % 6,
                   np.where(mx == g, (b - r) / diff + 2, (r - g) / diff + 4)) / 6.0
    weight = ((mx - mn) / (mx + 1e-6)) * mx
    palette = np.histogram(hue, bins=12, range=(0, 1), weights=weight)[0]

    def unit(v):
        return v / (np.linalg.norm(v) + 1e-8)
    return unit(structure), unit(palette.astype(np.float32)), unit(light)


def _similarity(a, b):
    return 0.50 * float(a[0] @ b[0]) + 0.28 * float(a[1] @ b[1]) \
        + 0.22 * float(a[2] @ b[2])


def twin_score(query_image, art_image):
    """Best structure/palette/light agreement across all crop x transform
    combinations, mapped to 0-10. Reports the winning combination so the
    composer can frame (and rotate) the artwork the same way."""
    query_feats = [(name, _features(_crop(query_image, box)))
                   for name, box in QUERY_CROPS]
    best = {"score": 0.0, "transform": "none", "art_crop": "full",
            "query_crop": "full", "raw": 0.0}
    transform_prior = {"none": 1.0, "mirror": 0.99,
                       "rot90": 0.955, "rot180": 0.955, "rot270": 0.955}
    crop_prior = {"full": 1.0, "center": 0.985, "upper": 0.975,
                  "q1": 0.945, "q2": 0.945, "q3": 0.945, "q4": 0.945}
    for transform_name, op in TRANSFORMS:
        transformed = art_image if op is None else art_image.transpose(op)
        for crop_name, box in ART_CROPS:
            art_feats = _features(_crop(transformed, box))
            for query_name, feats in query_feats:
                raw = (_similarity(feats, art_feats)
                       * transform_prior[transform_name] * crop_prior[crop_name])
                if raw > best["raw"]:
                    score = max(0.0, min(10.0, (raw - 0.40) / 0.45 * 10))
                    best = {"score": round(score, 1),
                            "transform": transform_name,
                            "art_crop": crop_name, "query_crop": query_name,
                            "raw": round(raw, 4)}
    return best


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        raise SystemExit("usage: score.py photo.jpg art.jpg")
    result = twin_score(Image.open(sys.argv[1]), Image.open(sys.argv[2]))
    print(f"twin {result['score']}/10  transform={result['transform']} "
          f"art_crop={result['art_crop']} query_crop={result['query_crop']} "
          f"(raw {result['raw']})")
