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
6. **Same framing, or crop until it is.** Zoom the artwork so its subject fills
   the frame like the photo's subject does. Tight crops of a large canvas (a
   single roundel, one face, one gesture) are often the strongest match.
7. **Two landscapes stack; portrait subjects sit side by side.**
8. **No quips in the caption.** First line is the artwork's name, then
   👤 artist / 🖌 medium / 📅 year / 🏛 museum, pop identity in the hashtags.
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
- **The corpus is the real constraint.** The reference feed leans heavily on
  modern and abstract work (Kandinsky, expressionists, Cubists, pointillists)
  that our Met + Cleveland public-domain harvest barely contains. Expanding
  into pre-1930 modernism is worth more than any scoring tweak.
