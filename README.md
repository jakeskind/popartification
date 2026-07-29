# popartification

Find the painting that looks *exactly* like any photo — the engine behind
the @artbutmakeitculture concept.

## How to use it

**Open an issue and paste a photo in the body.** The bot downloads it, searches
~30K public-domain paintings (Met, Art Institute of Chicago, Cleveland Museum
of Art, plus Muse's famous-works canon), and replies on the issue with a
contact sheet and ranking.

Matching axes: Apple Vision image fingerprints (structure), body-pose
skeletons (limb-angle exactness), face gaze direction, color palette, and
optional title similarity.

## Workflows

- **Build the art index** — harvests the museum open APIs, extracts features
  on a macOS runner, and publishes the index as the `index` release asset.
  Runs monthly; trigger manually after changing scripts.
- **Match an image to art** — runs on every new issue.

## Local use

```bash
python3 scripts/artmatch_harvest.py aic cleveland met
python3 scripts/artmatch.py --build
python3 scripts/artmatch.py photo.jpg --top 6
```

Cache lives in `~/.artmatch` (override with `ARTMATCH_HOME`).
