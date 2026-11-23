from conftest import fake_words

from viralbot.editing.cropper import x_expression
from viralbot.editing.subtitles import build_ass, group_words
from viralbot.editing.timeline import Timeline, build_timeline

TEXT = ("Why do most people quit? The truth is they never had a plan. " * 6).strip()


def test_silence_removed_and_no_words_lost():
    words = fake_words(TEXT, pause_every=8, pause=1.5)
    tl = build_timeline(words, words[0]["s"], words[-1]["e"], 999, remove_silence=True, max_pause=0.45,
                        min_len=20, max_len=45)
    assert len(tl.keeps) > 1
    raw = words[-1]["e"] - words[0]["s"]
    assert tl.duration < raw
    # every word inside the kept range is still present in full
    kept = [w for w in words if tl.to_output(w["s"]) is not None and tl.to_output(w["e"]) is not None]
    last_kept_idx = max(i for i, w in enumerate(words) if w in kept)
    assert len(kept) == last_kept_idx + 1


def test_max_length_enforced_on_sentence_boundary():
    words = fake_words(TEXT * 2)
    tl = build_timeline(words, 0, words[-1]["e"], 999, remove_silence=True, max_pause=0.45, min_len=20, max_len=45)
    assert 20 <= tl.duration <= 45


def test_too_short_falls_back_to_original_pacing():
    words = fake_words("one two three four five six seven eight", pause_every=1, pause=3)
    tl = build_timeline(words, 0, words[-1]["e"], 999, remove_silence=True, max_pause=0.4, min_len=20, max_len=45)
    assert len(tl.keeps) == 1


def test_timeline_mapping():
    tl = Timeline([(10, 15), (20, 30)])
    assert tl.duration == 15
    assert tl.to_output(12) == 2
    assert tl.to_output(17) is None
    assert tl.to_output(25) == 10
    assert tl.cut_points() == [5]


def test_ass_highlights_and_emphasis():
    words = fake_words("Nobody tells you the truth about money. It is crazy!")
    tl = Timeline([(0, 30)])
    ass = build_ass(words, tl, ["truth", "crazy"], font="DejaVu Sans", banner="Money truth")
    assert "Style: Sub" in ass and "Banner" in ass
    assert "&H0000E5FF&" in ass        # active-word highlight
    assert "&H0066FF66&" in ass        # emphasis colour
    assert "TRUTH" in ass and "CRAZY!" in ass
    assert len(group_words(words, 3)) >= 4


def test_x_expression_piecewise():
    expr = x_expression([(0, 0.3), (2, 0.3), (2.6, 0.7)], 1920, 608)
    assert expr.count("gte(t,") == 3
    assert x_expression([], 1920, 608) == str((1920 - 608) // 2)


def test_ass_excludes_words_outside_clip():
    before = fake_words("intro words that are not part of the clip", start=0)
    inside = fake_words("this sentence is inside the clip", start=50)
    after = fake_words("outro words after the clip", start=90)
    ass = build_ass(before + inside + after, Timeline([(49.9, 53)]), [], font="DejaVu Sans")
    assert "INSIDE" in ass
    for w in ("INTRO", "OUTRO"):
        assert w not in ass
