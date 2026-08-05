# Editorial lessons

Every correction from a real review becomes a rule here, and `judge.py` injects
the judging rules into Claude's prompt — so each pairing we critique
permanently sharpens the next one. Add a line whenever a call was wrong.

## Judging rules

1. **Direction must agree.** A club, scythe, bat, or limb swinging down-RIGHT
   should match a work whose implement also reads down-right. A mirrored pose
   is acceptable only when nothing else in the frame contradicts it.
2. **Sky agreement matters.** If the photo shows blue sky, prefer a work with
   blue sky over one with a dark treeline, even when the dominant field colour
   matches better. Presence of a feature beats average colour.
3. **Titles are half the joke.** A work titled *The Mower*, *The Clown*, or
   *Goat* against the matching subject beats a visually-equal work with a
   generic title. Prize wordplay on nicknames and slang (Messi = G.O.A.T.).
4. **Abstract is not a fallback — it is a first-class answer.** Roughly a third
   of the best pairings in the genre are non-figurative: a forced-perspective
   ball against Kandinsky's circles, a spinning red blur against concentric
   wood grain, a blue-and-white crowd against a pointillist mosaic, angular
   defending arms against a Cubist figure. Never require a face or a figure.
5. **Rotation is fair game.** A rotated or mirrored match is legitimate
   ("Goat (rotated)") and should be labelled as such.
6. **Same framing, or crop until it is — but never clip a defining feature.**
   If the tight crop would cut off the crown, hat, raised hand, or any part
   the picture is ABOUT, zoom BOTH halves out until it fits. Matching head
   scale by widening the photo's crop beats amputating the artwork's. Zoom the artwork so its subject fills
   the frame like the photo's subject does. Tight crops of a large canvas (a
   single roundel, one face, one gesture) are often the strongest match.
7. **Two landscapes stack; portrait subjects sit side by side.**
8. **Caption style.** First line is the artwork's name, then 👤 artist /
   🖌 medium / 📅 year / 🏛 museum, then (optionally) one short paragraph of
   background on the PAINTING ONLY — never explain the modern photo or the
   parallel; the audience already knows the meme, and spelling out the joke
   kills it — then plain words in a single bracket list —
   \[jimothy, seattle raccoon, …] — instead of hashtags. No quips.
9. **Emotion counts as much as geometry.** Grief, taunting, exhaustion,
   triumph, isolation — the feeling should rhyme, not just the limbs.

## Gesture archetypes (what actually gets matched)

Observed from the reference feed. Classify the photo into one of these and hunt
art in the same family — this is how a human does it, and it beats raw
embedding similarity.

| Archetype | Sports instance | Art family |
| --- | --- | --- |
| Arms outstretched (cruciform) | penalty appeal, celebration | saints, Christ, martyrdoms, Ascension |
| Tender head-cradle, eyes closed | consoling a teammate | Madonna and Child, Pietà, Deposition |
| Standing authority, arms crossed | posed portrait, captain | Napoleon, state portraits |
| Isolated kneeling figure in a crowd | despair after a miss | Christ before Pilate, martyrdom scenes |
| One arm raised high | referee's card, appeal | Icarus, allegories, judgement scenes |
| Leg kicked high | tackle, collision | Toulouse-Lautrec dancers, bacchanals |
| Bodies tangled in a melee | scuffle, brawl | Goya's fights, battle paintings |
| Bent double, tool swinging | golf swing, follow-through | harvest, reapers, mowers |
| Two figures walking, tall + small | player with mascot | expressionist pairs, processions |
| Pure geometry / texture | ball in net, spinning blur, crowd pattern | Kandinsky, pointillism, mosaics, roundels |

10. **Reach for the canon first.** If a world-famous artwork fits the moment,
   it beats an obscure one — recognition is half the joy. An engagement photo
   wants Klimt's *The Kiss*, not an anonymous Madonna. Ask "what is the most
   famous painting of this subject?" before settling.
11. **Match the relationship, not just the pose.** A Madonna and Child is
   mother-and-child; it must never stand in for lovers. Check that the
   *relationship* depicted rhymes with the photo's.

18. **Dense paintings are anthologies — match the vignette, not the frame.**
   Bosch and Bruegel painted hundreds of scenes per canvas; any one of them can
   be the comp. The index now carries overlapping vignette tiles of the great
   anthology works as first-class candidates ("… (detail)"), and the strategy
   stage asks which anthology painting would contain the moment as a background
   scene. A pairing that uses 3% of a canvas is not a compromise — it is the
   genre's signature move.

## Architectural lessons

- **Pixel statistics have a ceiling.** Hand-tuned colour/pose features are good
  for *retrieval* (finding 30 plausible candidates fast) but bad at
  *judgement*. Broad retrieval + Claude's eye beats ever-finer weight tuning.
  Example: the golfer photo's upper third measures 0% blue because the hillside
  fills it, yet a human instantly sees "blue sky, like the painting."
- **Averaging destroys distinctive features.** A band that is part sky, part
  trees averages to mud. Measure presence, not means.
- **Exposure is not colour.** Compare hue (HSV), never raw RGB bins: a bright
  golden field and a dark golden field are the same palette.
- **Retrieval recall is the bottleneck.** Homer's *The Veteran in a New Field*
  was in the corpus but never reached the judge.
- **My own taxonomy caused a bad answer.** The archetype table mapped "tender
  head-cradle" to exactly one family — Madonna and Child — so an engagement
  photo retrieved Madonnas and the system looked stupid. Hardcoded
  gesture→family mappings must be multi-valued and keyed on the RELATIONSHIP,
  or they become blinkers.
- **The pixel ceiling was re-confirmed by score.py.** A hand-rolled twin
  score (HOG orientation grid + hue histogram + luminance, over all
  zoom/rotation combos) ranked Leonidas above the Man with the Golden Helmet
  for the Damon bust and picked nonsense transforms. It survives only as a
  weak prior shown to the judge; Claude's eye ranks, and the judge now
  LOCALIZES every match (crop boxes + rotation) so previews and posts show
  the exact twinning, not whole canvases.
- **Keyword retrieval finds obscure text-matches, not masterpieces.** "Odysseus"
  as a keyword returned a bronze mirror and a Zurich hall mural; the canonical
  answers (Man with the Golden Helmet, Leonidas, the Turner Ulysses) only
  surface when the strategy NAMES famous works and they are looked up by name.
  Every hypothesis must carry a `works` list of specific famous titles.
- **Structured "depicted action" search does not exist usefully.** Wikidata's
  depicts-tagging for kiss/embrace returns nothing, and Commons' subject
  categories are dominated by book scans. The working substitute is asking for
  the CANONICAL WORKS of that action by name (The Kiss, The Lovers, Cupid and
  Psyche) and looking those up.
- **Named-artwork lookup is mandatory.** The stack could search by keyword,
  by subject, and by visual similarity, but had no way to fetch a famous work
  BY NAME — so Klimt's *The Kiss* was unreachable (it lives in the Belvedere,
  which has no open API, and a "depicts" query cannot find a work by its own
  title). Wikidata label search fixed it; the artist name has to be stripped
  from the query because labels are just "The Kiss".
- **The corpus is the real constraint.** The reference feed leans heavily on
  modern and abstract work (Kandinsky, expressionists, Cubists, pointillists)
  that our Met + Cleveland public-domain harvest barely contains. Expanding
  into pre-1930 modernism is worth more than any scoring tweak.


## Case log

What won, and which axis carried it.

- **taylor** → *Virgin and Child* (Mino da Fiesole (Mino di Giovanni)). Hypotheses: image(0.35), context(0.3), both(0.25), image(0.1). Forget the ring — it's the Pietà pose that sealed the engagement.

- **taylor** → *The Stolen Kiss* (Jean Honoré Fragonard). Hypotheses: context(0.35), image(0.25), context(0.1), both(0.15). Some things never change: a stolen kiss, a diamond, and a cake big enough for the whole century.

- **query** → *Odysseus* (Lovis Corinth). Hypotheses: context(0.3), image(0.25), both(0.2), image(0.15). Matt Damon channels the original brooding plume — Corinth just beat him to the etching press by a century.

- **damon** → *Leonidas at Thermopylae* (Jacques-Louis David). Hypotheses: context(0.3), image(0.25), context(0.2), image(0.15). This is Sparta... I mean Ithaca.

- **obsession** → *Judith I* (Gustav Klimt). Hypotheses: both(0.35), context(0.2), image(0.2), image(0.15). She didn't just survive the night — she got her gold leaf and her glow-up.

- **obsession (girl, bloodied grin)** → *Man of Sorrows* (Aelbrecht Bouts, Fogg). Anomaly route won: blood streaming down the face, found via its home genre (Passion imagery) after archetype/visual routes missed 3 rounds. The emotional INVERSION (her glee vs his sorrow, same blood) carried the pairing — a perfect mirror was not required.

- **creature** → *Theseus and the Minotaur* (Canova, Antonio). Hypotheses: image(0.3), image(0.2), context(0.2), context(0.2). Half man, half bull, all HR violation — Theseus never had to deal with a glass door.

- **creature (black minotaur robot behind office door)** → *The Unexpected Answer* (Magritte, 1933). The shared anomaly was the DOOR itself — a dark creature-silhouette filling a doorway in a blank wall. Pipeline reached the Minotaur myth + a Newman abstract on its own; the Magritte came from asking 'what famous artwork is about this exact situation?' Titles that read like a caption for the photo ('The Unexpected Answer') are gold.

- **leopold (hedge fund manager, clean bust portrait)** → *The Fortune-Teller* (Georges de La Tour, Met). Winning combo: visual twin (young pale mark, serene half-smile) + title wordplay (fortune/hedge fund) + the painting's STORY as caption (smiling while every hand empties his pockets = risked and lost in the market). Composed zoomed out so the robbers stay visible, mirrored for direction. The subject's own story can be the concept axis.

- **jimothy (Seattle raccoon, short spine syndrome, speck on a lawn)** → *Christina's World* (Wyeth, MoMA). Brief was visual (small speck in vast monotonous field) + title honoring uniqueness/spine. Won on composition AND story: Christina Olson's muscular condition, crossing her field her own way. Position-matched both specks low-left with horizons up top.

- **park-bench lawyers (caught mid-affair)** → *The Kiss* (Rodin). Won on pose (the one famous SEATED kiss; her arm to his head mirrored the photo) and buried story: everyone reads it as romance but it's Paolo and Francesca — an illicit affair caught in the act, from The Gates of Hell. When the photo IS a scandal, find the artwork whose hidden story is the same scandal.

- **query** → *The Birth of Venus* (Sandro Botticelli). Hypotheses: image(0.25), image(0.25), context(0.2), context(0.15). Venus, but make it hydrotherapy — not a hair out of place.
