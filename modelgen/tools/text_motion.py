"""A motion asked for in words ("does a spinning kick", "waves hello") as a clip.

Text-to-motion models (NVIDIA Kimodo) are switched by config.yaml's characters.text_motion. Checked
on 2026-11-21: Kimodo's text encoder is an 8-billion-parameter language model (4096-wide prompt
embeddings, about 16 GB), more than this PC's 8 GB of video memory and, run on the processor, more
than its 15 GB of memory; with the switch on and no model reachable, and with the switch off (the
default), a request in words takes the library clip whose name fits it best.
"""

import re

from .. import config

# words in a request -> words in a library clip's name (Quaternius, Mixamo and the like)
SYNONYMS = {
    "idle": ["idle", "stand", "breath"], "walk": ["walk"], "run": ["run", "jog", "sprint"],
    "jump": ["jump", "hop", "leap"], "wave": ["wave", "hello", "greet"], "punch": ["punch", "jab", "hit", "attack"],
    "kick": ["kick"], "dance": ["dance"], "sit": ["sit", "chair"], "crouch": ["crouch", "sneak", "duck"],
    "fall": ["fall", "death", "die"], "roll": ["roll", "dodge"], "climb": ["climb"], "swim": ["swim"],
    "talk": ["talk", "speak", "conversation"], "yes": ["yes", "nod"], "no": ["no", "shake"],
    "clap": ["clap", "cheer", "celebrate"], "shoot": ["shoot", "gun", "pistol", "rifle"], "sword": ["sword", "slash"],
    "push": ["push"], "pick": ["pick", "grab", "lift"], "spin": ["spin", "turn"], "fight": ["fight", "boxing", "combat"],
}


def enabled():
    return bool(config.get("characters", "text_motion", False))


def words(text):
    return re.findall(r"[a-z]+", str(text or "").lower())


def closest_clip(request, clips):
    """The clip (from the names given) whose name fits the request in words best, or None."""
    asked = set(words(request))
    want = set(asked)
    for key, alts in SYNONYMS.items():
        if any(a.startswith(alt) for a in asked for alt in alts + [key]):  # "running" is "run"
            want |= set(alts) | {key}
    best, score = None, 0
    for name in clips:
        have = set(words(name.replace("_", " ")))
        s = len(have & want) * 2 + sum(1 for w in want if any(w in h for h in have))
        if s > score:
            best, score = name, s
    return best


def motion_for(request, clips):
    """{"clip": name or None, "how": ...} for a motion asked for in words."""
    clip = closest_clip(request, clips)
    how = "the closest library clip"
    if enabled():
        how += " (text-to-motion is switched on, but no text-to-motion model can run on this PC)"
    return {"clip": clip, "how": how}
