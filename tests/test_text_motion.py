"""A motion in words picks the closest library clip (tools/text_motion.py)."""

from modelgen.tools import text_motion as tm

CLIPS = ["Idle_Loop", "Walk_Loop", "Jog_Fwd_Loop", "Sprint_Loop", "Jump_Start", "Punch_Jab", "Kick_Spin",
         "Wave_Hello", "Sit_Chair_Idle", "Death_Back"]


def test_words_pick_the_clip_that_fits():
    assert tm.closest_clip("does a spinning kick", CLIPS) == "Kick_Spin"
    assert tm.closest_clip("waves hello to a friend", CLIPS) == "Wave_Hello"
    assert tm.closest_clip("running fast", CLIPS) in ("Jog_Fwd_Loop", "Sprint_Loop")
    assert tm.closest_clip("sits down", CLIPS) == "Sit_Chair_Idle"
    assert tm.closest_clip("falls over dead", CLIPS) == "Death_Back"


def test_nothing_that_fits_gives_none():
    assert tm.closest_clip("plays the violin", CLIPS) is None


def test_text_motion_is_off_by_default_and_says_how_it_chose():
    assert not tm.enabled()
    assert tm.motion_for("a punch", CLIPS) == {"clip": "Punch_Jab", "how": "the closest library clip"}
