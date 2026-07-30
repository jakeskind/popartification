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
arms outstretched (cruciform) -> saints, Christ, martyrdom, ascension
tender head-cradle, eyes closed -> Madonna and Child, Pieta, deposition
standing authority, arms crossed -> Napoleon, state portraits
isolated kneeling figure in a crowd -> Christ before Pilate, martyrdom
one arm raised high -> Icarus, allegory, judgement
leg kicked high -> dancers, bacchanal
bodies tangled in a melee -> brawls, battles
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
                "Think about which axis carries this pairing:\n"
                "- IMAGE: pose, gesture, composition, palette, texture. Wins when the "
                "photo has a striking, recognisable form.\n"
                "- CONTEXT: what the moment MEANS — the event, the film being "
                "premiered, the rivalry, the nickname, the myth the subject evokes. "
                "Wins when a title or subject can land a joke or resonance (a "
                "Spider-Man premiere wants Arachne; Messi wants a goat; a clownish "
                "figure wants a painting titled 'The Clown').\n"
                "- BOTH: the ideal — a work that looks like the photo AND means "
                "something about it.\n\n"
                "Give 3 or 4 competing hypotheses. Make at least one a pure IMAGE "
                "hypothesis and at least one a pure CONTEXT hypothesis (dig for "
                "mythology, etymology, wordplay, the film's title, the team's mascot, "
                "the athlete's epithet). Weights sum to 1.0.\n\n"
                'Reply with STRICT JSON only:\n'
                '{"read": "<one sentence on what this image is and what it wants>", '
                '"hypotheses": [{"axis": "image|context|both", "idea": "<the leap>", '
                '"keywords": ["...", "..."], "weight": 0.4}, ...]}'},
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


def used_works():
    """Works already used in a published pairing — the case log is the ledger.
    A feed that keeps reaching for the same Madonna gets boring fast."""
    path = Path(__file__).resolve().parent.parent / "LESSONS.md"
    if not path.exists() or "## Case log" not in path.read_text():
        return set()
    log = path.read_text().split("## Case log", 1)[1]
    return {title.strip().lower()
            for title in re.findall(r"\*([^*]+)\*", log)}


def judge(query_path, context="", pool=16, keep=5, no_live=False):
    key = anthropic_key()
    if not key:
        raise SystemExit("no ANTHROPIC_API_KEY available")
    already_used = used_works()

    # Reasoning first: what kind of match does this image want?
    plan = plan_strategy(key, query_path, context, lessons_for_prompt())
    hypotheses = plan.get("hypotheses") or []
    if plan.get("read"):
        context = f"{context} | read: {plan['read']}"

    visual = match(query_path, top=pool)
    keywords, archetype = describe_for_search(key, query_path, context)
    # Every hypothesis contributes its own search vocabulary.
    for h in hypotheses:
        keywords += [k for k in h.get("keywords", []) if k not in keywords]
    if archetype:
        context = f"{context} | gesture archetype: {archetype}"

    # Merge in title-matched works the visual pass missed.
    seen = {c[2] for c in visual}
    candidates = list(visual)
    for wid, work in keyword_candidates(keywords, limit=pool):
        if wid in seen or not (IMAGES / f"{wid}.jpg").exists():
            continue
        seen.add(wid)
        candidates.append((0.0, {"keyword": 1.0}, wid, work, None, "none"))
    local_count = len(candidates)

    # …and search the live museum APIs, across every object type. The local
    # index is finite and paintings-only; the web is neither. This is what
    # finds an Arachne etching for a Spider-Man premiere.
    if not no_live:
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
    for index, (_, _, wid, work, entry, transform) in enumerate(candidates, start=1):
        if work.get("path"):
            image, kind = Image.open(work["path"]).convert("RGB"), "live"
        else:
            image, kind = matched_image(wid, entry, transform)
        line = (f"Candidate {index}: “{work['title']}” — {work['artist'] or 'unknown'}"
                + (f", {work['year']}" if work["year"] else "")
                + (f" · {work['museum']}" if work["museum"] else "")
                + (f" [{work['medium']}]" if work.get("medium") else "")
                + (" (cropped detail)" if kind not in ("full", "live") else ""))
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
        f"\nRank the best {keep}. Default weighting is visual 60 / concept 40, but "
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
    return verdict, candidates


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    context = args[args.index("--context") + 1] if "--context" in args else ""
    pool = int(args[args.index("--pool") + 1]) if "--pool" in args else 16
    keep = int(args[args.index("--keep") + 1]) if "--keep" in args else 5
    judge(args[0], context, pool, keep, no_live="--no-live" in args)
