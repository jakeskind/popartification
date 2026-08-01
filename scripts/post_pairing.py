#!/usr/bin/env python3
"""Post a pairing to @popartification with the house caption format:
artwork details on emoji lines (like Muse's Painting of the Day), pop-culture
identity in the hashtags.

Usage:
    python3 scripts/post_pairing.py <public-image-url> \
        --title "Virgin and Child (detail)" \
        --artist "Mino da Fiesole" \
        --medium "Marble relief" \
        --year "15th century" \
        --museum "The Metropolitan Museum of Art" \
        --tags "arianagrande yesand" \
        [--dry-run]

Needs POPARTIFICATION_IG_TOKEN (env or ~/.muse-marketing.env). The image URL
must be public JPEG — showcase/ raw URLs on this repo qualify.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GRAPH = "https://graph.instagram.com/v23.0"
BASE_WORDS = ["life imitates art history", "art history", "pop culture"]


def token():
    if os.environ.get("POPARTIFICATION_IG_TOKEN"):
        return os.environ["POPARTIFICATION_IG_TOKEN"]
    env_file = Path.home() / ".muse-marketing.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("POPARTIFICATION_IG_TOKEN="):
                return line.split("=", 1)[1].strip()
    return None


def build_caption(args):
    # House style: the artwork's name IS the first line — no quips — then the
    # details on emoji lines, pop identity in the hashtags.
    lines = []
    if args.get("title"):
        lines.append(args["title"])
    if args.get("artist"):
        lines.append(f"👤 {args['artist']}")
    if args.get("medium"):
        lines.append(f"🖌 {args['medium']}")
    if args.get("year"):
        lines.append(f"📅 {args['year']}")
    if args.get("museum"):
        lines.append(f"🏛 {args['museum']}")

    if args.get("info"):
        lines += ["", args["info"]]

    words = [w.strip() for w in (args.get("tags") or "").split(",") if w.strip()]
    lines += ["", "[" + ", ".join(words + BASE_WORDS) + "]"]
    return "\n".join(lines)


def publish(image_url, caption, access_token):
    def graph(path, payload):
        request = urllib.request.Request(
            f"{GRAPH}/{path}", data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)

    me = json.load(urllib.request.urlopen(
        f"{GRAPH}/me?fields=user_id,username&access_token="
        + urllib.parse.quote(access_token)))
    ig_id = me.get("user_id") or me.get("id")
    print(f"posting as @{me.get('username')}")
    container = graph(f"{ig_id}/media", {"image_url": image_url, "caption": caption})
    for attempt in range(6):
        try:
            out = graph(f"{ig_id}/media_publish", {"creation_id": container["id"]})
            print("published:", json.dumps(out))
            return
        except urllib.error.HTTPError as error:
            body = error.read().decode()[:200]
            if attempt == 5:
                raise SystemExit(f"publish failed: {body}")
            print(f"not ready (attempt {attempt + 1})")
            time.sleep(10)


import urllib.parse  # noqa: E402


def main():
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit(__doc__)
    image_url = argv[0]
    args = {}
    for flag in ("title", "artist", "medium", "year", "museum", "tags", "info"):
        if f"--{flag}" in argv:
            args[flag] = argv[argv.index(f"--{flag}") + 1]
    caption = build_caption(args)
    print("──── caption ────\n" + caption + "\n─────────────────")

    # Never publish stale CDN bytes: if --verify-file is given, the public URL
    # must serve EXACTLY that file before we hand it to Instagram. Reusing a
    # filename after an edit means raw.githubusercontent can serve the old
    # cached image for minutes — long enough to repost the mistake.
    if "--verify-file" in sys.argv:
        import hashlib, time, urllib.request
        expected = hashlib.md5(
            open(sys.argv[sys.argv.index("--verify-file") + 1], "rb").read()).hexdigest()
        for attempt in range(10):
            remote = urllib.request.urlopen(image_url, timeout=60).read()
            if hashlib.md5(remote).hexdigest() == expected:
                print("verified: URL serves the intended bytes")
                break
            print(f"URL still serving stale bytes (attempt {attempt + 1}) — waiting…")
            time.sleep(30)
        else:
            raise SystemExit("URL never served the intended file — not posting.")
    if "--dry-run" in argv:
        print("(dry run — not posting)")
        return
    access_token = token()
    if not access_token:
        raise SystemExit("POPARTIFICATION_IG_TOKEN not configured")
    publish(image_url, caption, access_token)


if __name__ == "__main__":
    main()
