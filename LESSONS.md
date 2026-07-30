# Editorial lessons

Every correction from a real review becomes a rule here, and `judge.py` injects
this file into Claude's prompt — so each pairing we critique permanently
sharpens the next one. Add a line whenever a call was wrong.

## Judging rules

1. **Direction must agree.** A club, scythe, bat, or limb swinging down-RIGHT
   should match a work whose implement also reads down-right. A mirrored pose
   is acceptable only when nothing else in the frame contradicts it.
2. **Sky agreement matters.** If the photo shows blue sky, prefer a work with
   blue sky over one with a dark treeline, even when the dominant field colour
   matches better. Presence of the feature beats average colour.
3. **Titles are half the joke.** A work titled *The Mower*, *The Clown*, or
   *Goat* against the matching subject beats a visually-equal work with a
   generic title. Prize wordplay on nicknames and slang (Messi = G.O.A.T.).
4. **Abstract is welcome.** If shapes and colours echo the photo, the
   unexpectedness is a feature. Never require a face-to-face match.
5. **Rotation is fair game.** ArtButSports rotates works when it makes the
   forms align ("Goat (rotated)"); a rotated or mirrored match is legitimate
   and should be labelled as such.
6. **Same framing, or crop until it is.** The artwork side should be zoomed so
   the subject fills the frame like the photo's subject does.
7. **Two landscapes stack; portrait subjects sit side by side.**
8. **No quips in the caption.** First line is the artwork's name, then
   👤 artist / 🖌 medium / 📅 year / 🏛 museum, pop identity in the hashtags.

## Architectural lessons

- **Pixel statistics have a ceiling.** Hand-tuned colour/pose features are good
  for *retrieval* (finding 30 plausible candidates fast) but bad at *judgement*.
  Broad retrieval + Claude's eye beats ever-finer weight tuning. Example: the
  golfer photo's upper third measures 0% blue because the hillside fills it,
  yet a human instantly sees "blue sky, like the painting."
- **Averaging destroys distinctive features.** A band that is part sky, part
  trees averages to mud. Measure presence, not means.
- **Exposure is not colour.** Compare hue (HSV), never raw RGB bins: a bright
  golden field and a dark golden field are the same palette.
- **Retrieval recall is the bottleneck.** Homer's *The Veteran in a New Field*
  was in the corpus but never reached the judge. Keep the candidate pool wide
  and the keyword list generous.
