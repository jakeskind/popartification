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
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artmatch import match, matched_image, keyword_candidates, IMAGES  # noqa: E402
from live_search import search_museums  # noqa: E402


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


def claude(key, payload):
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=300) as response:
        blocks = json.load(response)["content"]
        texts = [b["text"] for b in blocks if b.get("type") == "text"]
        if not texts:
            raise SystemExit("judge returned no text (token budget exhausted?)")
        return texts[-1]


ARCHETYPES = """
Each gesture has SEVERAL possible art families — which one is right depends on
the RELATIONSHIP and the story, never the pose alone. Pick by relationship.

faces pressed together, hands cradling a face
  lovers/romance  -> The Kiss (Klimt), The Kiss (Rodin/Hayez/Munch), Cupid and
                     Psyche, betrothal and marriage portraits, Chagall's lovers
  parent/child    -> Madonna and Child, Holy Family
  grief           -> Pieta, Deposition, Lamentation
  reunion         -> prodigal son, Jacob and Esau, homecomings
arms outstretched (cruciform) -> saints, Christ, martyrdom, ascension, Icarus
standing authority, arms crossed -> Napoleon, state and court portraits
isolated kneeling figure in a crowd -> Christ before Pilate, martyrdom
one arm raised high -> Icarus, allegory, judgement, Liberty
leg kicked high -> dancers, bacchanal, Toulouse-Lautrec
bodies tangled in a melee -> brawls, battles, Goya
bent double with a tool -> harvest, reapers, mowers
two figures walking, tall and small -> processions, expressionist pairs
pure geometry or texture -> abstraction, pointillism, mosaic, roundel
"""

def describe_for_search(key, query_path, context):
    """Ask Claude to do what a human curator does: name the gesture archetype,
    then name the words a painting of that gesture would be TITLED with. The
    keywords drive a title-based retrieval pass alongside the visual one."""
    reply = claude(key, {"model": "claude-sonnet-5", "max_tokens": 400,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text":
                f"Pop-culture context: {context or 'unknown'}.\n\n"
                f"Common gesture archetypes and the art families that hold them:{ARCHETYPES}\n"
                "Looking at this photo: (1) which archetype fits (or describe a "
                "better one), and (2) list the words most likely to appear in the "
                "TITLE of an artwork depicting the same gesture, scene, or subject "
                "— include religious, mythological and allegorical vocabulary, and "
                "if the image is mostly pattern or motion, include abstract terms "
                "(composition, circles, spiral, rhythm). Reply as JSON only: "
                '{"archetype": "...", "keywords": ["...", "..."]}'},
            image_block(Image.open(query_path)),
        ]}]})
    data = loads_loose(reply)
    if data.get("archetype"):
        print(f"archetype: {data['archetype']}")
    return data.get("keywords", []), data.get("archetype", "")


def loads_loose(reply):
    """Parse JSON from a model reply, tolerating the usual damage: markdown
    fences, prose around the object, and unescaped double quotes inside string
    values (a caption mentioning a "title" breaks strict JSON)."""
    text = reply.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")
    end = text.rfind("}")
    if end < start:
        # Truncated mid-object (token cap): salvage by closing the structure
        # after the last complete element.
        cut = max(text.rfind("},"), text.rfind('",'), text.rfind("],"))
        if cut == -1:
            raise ValueError("reply truncated before any complete element")
        text = text[start:cut + 1].rstrip(",")
        opens = text.count("{") - text.count("}")
        brackets = text.count("[") - text.count("]")
        text += "]" * max(0, brackets) + "}" * max(0, opens)
    else:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Escape double quotes that sit inside a value rather than delimiting it.
    repaired, in_string, escaped = [], False, False
    for index, char in enumerate(text):
        if escaped:
            repaired.append(char); escaped = False; continue
        if char == "\\":
            repaired.append(char); escaped = True; continue
        if char == '"':
            if not in_string:
                in_string = True; repaired.append(char); continue
            # Closing quote only when the next meaningful char structurally ends
            # the string; otherwise it is content and must be escaped.
            rest = text[index + 1:].lstrip()
            if rest[:1] in (",", "}", "]", ":"):
                in_string = False; repaired.append(char)
            else:
                repaired.append('\\"')
            continue
        repaired.append(char)
    return json.loads("".join(repaired))


def lessons_for_prompt():
    """The accumulated house rules and case log, for injection into prompts."""
    path = Path(__file__).resolve().parent.parent / "LESSONS.md"
    if not path.exists():
        return ""
    text = path.read_text()
    parts = []
    if "## Judging rules" in text:
        body = text.split("## Judging rules", 1)[1].split("## Architectural")[0]
        parts.append("HOUSE RULES (learned from past reviews):\n" + body.strip())
    if "## Case log" in text:
        parts.append("PAST CASES:\n" + text.split("## Case log", 1)[1].strip()[:900])
    return "\n\n" + "\n\n".join(parts) if parts else ""


def plan_strategy(key, query_path, context, lessons=""):
    """The reasoning stage: before searching, decide WHAT KIND of match this
    image wants. Some pairings live on the image (pose, composition, palette);
    some live on the context (an event, a nickname, a myth the subject evokes);
    the best live on both. Claude enumerates several competing hypotheses, each
    with its own search vocabulary and a weight, and every one gets retrieved.

    This is what turns "Spider-Man premiere" into "Arachne" without being
    told — the concept route is pursued as a first-class hypothesis rather than
    hoped for as a side effect of describing the picture.
    """
    reply = claude(key, {"model": "claude-sonnet-5", "max_tokens": 3000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text":
                "You are the search strategist for an account that pairs pop-culture "
                "photos with artworks. Before any searching happens, decide what kind "
                "of match THIS image wants.\n\n"
                f"Context: {context or 'unknown — infer from the image'}\n"
                f"{lessons}\n"
                "There are ALWAYS several clever routes. Canvass them all before "
                "committing — a single obvious route is how a pairing ends up "
                "boring or wrong:\n"
                "- POSE/COMPOSITION: the shape of the moment. But name the "
                "RELATIONSHIP too (lovers? parent and child? rivals? grief?) — the "
                "same gesture belongs to different art families depending on it.\n"
                "- DEPICTED ACTION: search for artworks that literally show this "
                "action — kissing, embracing, cradling a face, leaping, mourning. "
                "This finds works whose TITLE says nothing useful.\n"
                "- EVENT/SUBJECT: the film premiered, the trophy won, the scandal.\n"
                "- TEAM, MASCOT, CITY, NICKNAME: a Chiefs player, a Cardinals "
                "outfielder, a Lions lineman, 'the GOAT', 'King James' — the emblem "
                "or epithet is a rich vein of imagery and wordplay.\n"
                "- MYTH/ALLEGORY: what myth is this moment a version of?\n"
                "- CANON: what is the single most FAMOUS artwork of this subject? "
                "Recognition is half the joy — prefer it when it fits.\n"
                "- PALETTE/ABSTRACT: colour fields, texture, geometry.\n\n"
                "Give 4 or 5 competing hypotheses drawn from DIFFERENT routes above "
                "(never two of the same kind). At least one must be pure "
                "pose/composition and at least one pure concept. Weights sum to "
                "1.0.\n\n"
                "CRITICAL: for each hypothesis also NAME 2-3 specific famous "
                "artworks (\"Title Artist\") that fit it — e.g. 'The Man with the "
                "Golden Helmet Rembrandt', 'Leonidas at Thermopylae David', "
                "'Ulysses deriding Polyphemus Turner'. Generic keywords retrieve "
                "obscure text-matches; named masterpieces are retrieved directly "
                "and are usually the winning candidates.\n\n"
                'Reply with STRICT JSON only:\n'
                '{"read": "<one sentence on what this image is and what it wants>", '
                '"hypotheses": [{"axis": "image|context|both", "idea": "<the leap>", '
                '"keywords": ["...", "..."], '
                '"works": ["<Famous Title> <Artist>", "..."], "weight": 0.4}, ...]}'},
            image_block(Image.open(query_path)),
        ]}]})
    plan = loads_loose(reply)
    print(f"\nREAD: {plan.get('read', '')}")
    for h in plan.get("hypotheses", []):
        print(f"  [{h.get('axis', '?'):7}] w={h.get('weight', 0):.2f}  {h.get('idea', '')}")
        print(f"            → {', '.join(h.get('keywords', [])[:9])}")
    return plan


def record_case(query_label, plan, winner_work, verdict):
    """Append what won and which axis carried it, so future strategy calls see
    precedent. The file is injected into later prompts — the system's memory."""
    path = Path(__file__).resolve().parent.parent / "LESSONS.md"
    if not path.exists():
        return
    text = path.read_text()
    if "## Case log" not in text:
        text += "\n\n## Case log\n\nWhat won, and which axis carried it.\n"
    axes = ", ".join(f"{h.get('axis')}({h.get('weight')})"
                     for h in plan.get("hypotheses", [])[:4])
    entry = (f"\n- **{query_label}** → *{winner_work.get('title')}* "
             f"({winner_work.get('artist') or 'unknown'}). "
             f"Hypotheses: {axes}. {verdict.get('caption', '')}\n")
    path.write_text(text + entry)


ROTATE_OPS = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}


def boxed(image, box):
    """Crop by a [left, top, width, height] fraction box, defensively."""
    if not box or len(box) != 4:
        return image
    w, h = image.size
    left = min(max(box[0], 0.0), 0.95) * w
    top = min(max(box[1], 0.0), 0.95) * h
    width = max(box[2], 0.05) * w
    height = max(box[3], 0.05) * h
    return image.crop((int(left), int(top),
                       int(min(left + width, w)), int(min(top + height, h))))


def match_preview(query_image, art_image, row):
    """Side-by-side of the two images AFTER the judge's zoom/rotation — the
    user compares the actual match, not the whole canvases."""
    art = art_image
    rotate = int(row.get("rotate") or 0) % 360
    if rotate in ROTATE_OPS:
        art = art.transpose(ROTATE_OPS[rotate])
    left = boxed(query_image, row.get("query_box"))
    right = boxed(art, row.get("art_box"))
    HEIGHT, GAP = 460, 4
    tiles = []
    for tile in (left, right):
        width = max(1, int(tile.width * HEIGHT / tile.height))
        tiles.append(tile.resize((width, HEIGHT), Image.LANCZOS))
    canvas = Image.new("RGB", (tiles[0].width + tiles[1].width + GAP, HEIGHT),
                       (8, 8, 8))
    canvas.paste(tiles[0], (0, 0))
    canvas.paste(tiles[1], (tiles[0].width + GAP, 0))
    return canvas


def used_works():
    """Works already used in a published pairing — the case log is the ledger.
    A feed that keeps reaching for the same Madonna gets boring fast."""
    path = Path(__file__).resolve().parent.parent / "LESSONS.md"
    if not path.exists() or "## Case log" not in path.read_text():
        return set()
    log = path.read_text().split("## Case log", 1)[1]
    return {title.strip().lower()
            for title in re.findall(r"\*([^*]+)\*", log)}


def judge(query_path, context="", pool=16, keep=5, no_live=False, json_out=None):
    key = anthropic_key()
    if not key:
        raise SystemExit("no ANTHROPIC_API_KEY available")
    already_used = used_works()

    # Reasoning first: what kind of match does this image want?
    plan = plan_strategy(key, query_path, context, lessons_for_prompt())
    hypotheses = plan.get("hypotheses") or []
    if plan.get("read"):
        context = f"{context} | read: {plan['read']}"

    try:
        visual = match(query_path, top=pool)
    except Exception as error:  # noqa: BLE001 - no local index (cloud run)
        print(f"(no local index — live search only: {error})")
        visual = []
    keywords, archetype = describe_for_search(key, query_path, context)
    # Every hypothesis contributes its own search vocabulary.
    for h in hypotheses:
        keywords += [k for k in h.get("keywords", []) if k not in keywords]
    if archetype:
        context = f"{context} | gesture archetype: {archetype}"

    # Merge in title-matched works the visual pass missed.
    seen = {c[2] for c in visual}
    candidates = list(visual)
    try:
        for wid, work in keyword_candidates(keywords, limit=pool):
            if wid in seen or not (IMAGES / f"{wid}.jpg").exists():
                continue
            seen.add(wid)
            candidates.append((0.0, {"keyword": 1.0}, wid, work, None, "none"))
    except Exception:  # noqa: BLE001 - no local corpus
        pass
    local_count = len(candidates)

    # …and search the live museum APIs, across every object type. The local
    # index is finite and paintings-only; the web is neither. This is what
    # finds an Arachne etching for a Spider-Man premiere.
    named_works = [w for h in hypotheses for w in h.get("works", [])]
    if not no_live:
        from live_search import _wikidata_named
        for name in dict.fromkeys(named_works):
            for hit in _wikidata_named(name, 1):
                if hit["id"] in seen:
                    continue
                # Reuse the thumbnail path machinery from search_museums.
                from live_search import search_museums as _sm  # noqa: F401
                seen.add(hit["id"])
                candidates.append((0.0, {"named": 1.0}, hit["id"], {
                    "title": hit["title"], "artist": hit["artist"],
                    "year": hit["year"], "museum": hit["museum"],
                    "medium": hit.get("medium", ""), "image": hit["image"],
                    "hires": hit.get("hires"), "url": hit["image"]}, None, "none"))
        for hit in search_museums(keywords, limit=pool):
            if hit["id"] in seen:
                continue
            seen.add(hit["id"])
            work = {"title": hit["title"], "artist": hit["artist"],
                    "year": hit["year"], "museum": hit["museum"],
                    "medium": hit.get("medium", ""),
                    "image": hit["image"], "hires": hit.get("hires"),
                    "path": str(hit["path"])}
            candidates.append((0.0, {"live": 1.0}, hit["id"], work, None, "none"))
    if already_used:
        before = len(candidates)
        candidates = [c for c in candidates
                      if c[3].get("title", "").strip().lower() not in already_used]
        if before != len(candidates):
            print(f"(skipped {before - len(candidates)} already-used work(s))")
    print(f"judging {len(candidates)} candidates ({len(visual)} visual + "
          f"{local_count - len(visual)} by title + {len(candidates) - local_count} live)")

    lessons = lessons_for_prompt()

    content = [
        {"type": "text", "text":
            "You are the editor of an account that pairs pop-culture photos with "
            "artworks that look strikingly identical (in the spirit of ArtButSports). "
            f"Pop-culture context: {context or 'unknown — infer from the image'}."
            f"{lessons}\n\n"
            "THE QUERY IMAGE:"},
        image_block(Image.open(query_path)),
        {"type": "text", "text": "THE CANDIDATES (details may be crops of larger works):"},
    ]
    from score import twin_score
    query_pil = Image.open(query_path).convert("RGB")
    candidate_images = {}
    for index, (_, _, wid, work, entry, transform) in enumerate(candidates, start=1):
        if work.get("path"):
            image, kind = Image.open(work["path"]).convert("RGB"), "live"
        elif work.get("url"):
            try:
                request = urllib.request.Request(
                    work["url"], headers={"User-Agent": "MuseArtMatch/2.0"})
                with urllib.request.urlopen(request, timeout=90) as response:
                    image = Image.open(io.BytesIO(response.read())).convert("RGB")
                kind = "live"
            except Exception:  # noqa: BLE001 - drop unfetchable candidates
                continue
        else:
            image, kind = matched_image(wid, entry, transform)
        candidate_images[index] = image
        twin = twin_score(query_pil, image)
        line = (f"Candidate {index}: “{work['title']}” — {work['artist'] or 'unknown'}"
                + (f", {work['year']}" if work["year"] else "")
                + (f" · {work['museum']}" if work["museum"] else "")
                + (f" [{work['medium']}]" if work.get("medium") else "")
                + (" (cropped detail)" if kind not in ("full", "live") else "")
                + f" | computed-twin {twin['score']}/10"
                + (f" (suggests {twin['transform']}, {twin['art_crop']} crop)"
                   if twin["transform"] != "none" or twin["art_crop"] != "full"
                   else ""))
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
        "A computed-twin score accompanies each candidate: it is style-blind "
        "structure/palette/light agreement across zooms and rotations — treat it "
        "as a weak prior only; trust your own eye for pose, gaze and meaning. "
        "Impressionist and abstract works are first-class: score their visual "
        "match on shape-echo and palette-echo, never on realism.\n\n"
        "For every ranked candidate, also LOCALIZE the match — the crop of each "
        "image that should sit side by side so the twinning is undeniable. "
        "Boxes are [left, top, width, height] as fractions of the image. "
        "rotate is degrees counterclockwise applied to the ARTWORK BEFORE its "
        "box is taken (0 unless a rotation genuinely improves the match).\n\n"
        f"Reply with STRICT JSON only:\n"
        '{"ranking": [{"candidate": <n>, "visual": <0-10>, "concept": <0-10>, '
        '"why": "<one short sentence>", '
        '"query_box": [l, t, w, h], "art_box": [l, t, w, h], "rotate": 0}, ...], '
        '"winner": <n>, "caption": "<one witty line for the post, no hashtags>"}'
        f"\nRank the best {keep}, and the top 3 MUST come from three different art "
        "families or subject types — if several Madonnas (or several of anything) "
        "are the closest visually, keep only the best one and fill the rest with "
        "genuinely different ideas. Sanity-check the relationship: a mother-and-"
        "child work can never stand in for lovers, and vice versa.\n"
        f"Default weighting is visual 60 / concept 40, but "
        f"this image was read as: {plan.get('read', 'n/a')} — with hypotheses "
        + "; ".join(f"{h.get('axis')} ({h.get('weight')}): {h.get('idea')}"
                    for h in hypotheses)
        + ". Weight the axes accordingly."})

    reply = claude(key, {"model": "claude-sonnet-5", "max_tokens": 4000,
                         "messages": [{"role": "user", "content": content}]})
    verdict = loads_loose(reply)

    print("\nJUDGE RANKING (visual 60 / concept 40):")
    for row in verdict["ranking"]:
        _, _, wid, work, entry, _tf = candidates[row["candidate"] - 1]
        kind = entry["kind"] if entry else "full"
        print(f"  #{row['candidate']} v={row['visual']} c={row['concept']}  "
              f"{work['title']} — {work['artist']} [{kind}]")
        print(f"      {row['why']}")
    winner = candidates[verdict["winner"] - 1]
    print(f"\nWINNER: {winner[3]['title']} — {winner[3]['artist']} ({winner[2]})")
    if winner[3].get("hires"):
        print(f"  hi-res: {winner[3]['hires']}")
    if winner[3].get("medium"):
        print(f"  medium: {winner[3]['medium']}")
    print(f"CAPTION IDEA: {verdict['caption']}")
    try:
        record_case(Path(query_path).stem, plan, winner[3], verdict)
    except Exception:  # noqa: BLE001 - logging must never break a run
        pass

    if json_out:
        out = Path(json_out)
        options = []
        for label, row in zip("ABCDE", verdict["ranking"]):
            _, _, wid, work, entry, transform = candidates[row["candidate"] - 1]
            thumb = out.with_name(f"{out.stem}_{label}.jpg")
            try:
                art = candidate_images.get(row["candidate"])
                if art is None:
                    art, _ = matched_image(wid, entry, transform)
                preview = match_preview(query_pil, art, row)
                preview.save(thumb, "JPEG", quality=88)
            except Exception:  # noqa: BLE001
                thumb = None
            options.append({
                "label": label, "wid": wid,
                "title": work.get("title", ""), "artist": work.get("artist", ""),
                "year": work.get("year", ""), "museum": work.get("museum", ""),
                "medium": work.get("medium", ""),
                "hires": work.get("hires") or work.get("image"),
                "query_box": row.get("query_box"), "art_box": row.get("art_box"),
                "rotate": row.get("rotate", 0),
                "visual": row.get("visual"), "concept": row.get("concept"),
                "why": row.get("why", ""),
                "thumb": thumb.name if thumb else None,
                "winner": row["candidate"] == verdict.get("winner"),
            })
        out.write_text(json.dumps(
            {"read": plan.get("read", ""), "caption": verdict.get("caption", ""),
             "options": options}, indent=2))
        print(f"wrote {out}")
    return verdict, candidates


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    context = args[args.index("--context") + 1] if "--context" in args else ""
    pool = int(args[args.index("--pool") + 1]) if "--pool" in args else 16
    keep = int(args[args.index("--keep") + 1]) if "--keep" in args else 5
    json_out = args[args.index("--json") + 1] if "--json" in args else None
    judge(args[0], context, pool, keep, no_live="--no-live" in args,
          json_out=json_out)
