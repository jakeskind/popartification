#!/usr/bin/env python3
"""Claude judge: re-rank the matcher's visual candidates on BOTH axes that
make a pairing land —

  visual twinning : pose, gaze, framing, composition ("literally the same")
  conceptual punch: how the artwork's TITLE and subject resonate with the
                    pop-culture moment (a painting called "The Clown" beats a
                    visually-equal painting called "Portrait of a Man" when
                    the subject is clownable)

Usage:
    python3 scripts/judge.py query.jpg --context "Ariana Grande in the
        'yes, and?' video, black and white, gazing down" [--pool 16] [--keep 5]

Needs ANTHROPIC_API_KEY (env, ~/.muse-marketing.env, or Muse's Secrets.plist).
"""

import base64
import io
import json
import os
import plistlib
import sys
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artmatch import match, matched_image  # noqa: E402


def anthropic_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env_file = Path.home() / ".muse-marketing.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    secrets = Path.home() / "Canvas/Canvas/Secrets.plist"
    if secrets.exists():
        return plistlib.loads(secrets.read_bytes()).get("ANTHROPIC_API_KEY")
    return None


def image_block(image, max_side=420):
    image = image.copy()
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=80)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buffer.getvalue()).decode()}}


def judge(query_path, context="", pool=16, keep=5):
    key = anthropic_key()
    if not key:
        raise SystemExit("no ANTHROPIC_API_KEY available")

    candidates = match(query_path, top=pool)

    content = [
        {"type": "text", "text":
            "You are the editor of an account that pairs pop-culture photos with "
            "artworks that look strikingly identical (in the spirit of ArtButSports). "
            f"Pop-culture context: {context or 'unknown — infer from the image'}.\n\n"
            "THE QUERY IMAGE:"},
        image_block(Image.open(query_path)),
        {"type": "text", "text": "THE CANDIDATES (details may be crops of larger works):"},
    ]
    for index, (_, _, wid, work, entry, transform) in enumerate(candidates, start=1):
        image, kind = matched_image(wid, entry, transform)
        line = (f"Candidate {index}: “{work['title']}” — {work['artist'] or 'unknown'}"
                + (f", {work['year']}" if work["year"] else "")
                + (f" · {work['museum']}" if work["museum"] else "")
                + (" (cropped detail)" if kind != "full" else ""))
        content.append({"type": "text", "text": line})
        content.append(image_block(image))

    content.append({"type": "text", "text":
        f"Score every candidate 0-10 on two axes:\n"
        "- visual: is it LITERALLY the same image — pose, gaze direction, head "
        "angle, framing, mood?\n"
        "- concept: does the TITLE or subject land a joke or resonance against "
        "the pop context? Prize WORDPLAY on nicknames and slang hard (a work "
        "titled 'Goat' for Messi = G.O.A.T., 'The Clown' for a clownable "
        "subject, a Madonna for a pop idol). Be open-minded about ABSTRACT "
        "works — if the shapes and colors echo the photo, that unexpectedness "
        "is a feature, not a bug.\n\n"
        f"Reply with STRICT JSON only:\n"
        '{"ranking": [{"candidate": <n>, "visual": <0-10>, "concept": <0-10>, '
        '"why": "<one short sentence>"}, ...], '
        '"winner": <n>, "caption": "<one witty line for the post, no hashtags>"}'
        f"\nRank the best {keep}, weighting visual 60 / concept 40."})

    payload = {"model": "claude-sonnet-5", "max_tokens": 900,
               "messages": [{"role": "user", "content": content}]}
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=300) as response:
        blocks = json.load(response)["content"]
        # Reasoning models may lead with a thinking block — take the text one.
        reply = next(b["text"] for b in blocks if b.get("type") == "text")

    reply = reply[reply.index("{"): reply.rindex("}") + 1]
    verdict = json.loads(reply)

    print("\nJUDGE RANKING (visual 60 / concept 40):")
    for row in verdict["ranking"]:
        _, _, wid, work, entry, _tf = candidates[row["candidate"] - 1]
        kind = entry["kind"] if entry else "full"
        print(f"  #{row['candidate']} v={row['visual']} c={row['concept']}  "
              f"{work['title']} — {work['artist']} [{kind}]")
        print(f"      {row['why']}")
    winner = candidates[verdict["winner"] - 1]
    print(f"\nWINNER: {winner[3]['title']} — {winner[3]['artist']} ({winner[2]})")
    print(f"CAPTION IDEA: {verdict['caption']}")
    return verdict, candidates


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    context = args[args.index("--context") + 1] if "--context" in args else ""
    pool = int(args[args.index("--pool") + 1]) if "--pool" in args else 16
    keep = int(args[args.index("--keep") + 1]) if "--keep" in args else 5
    judge(args[0], context, pool, keep)
