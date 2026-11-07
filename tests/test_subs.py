from kirinuki import subs
from kirinuki.transcribe import Line


def test_is_english():
    assert subs.is_english("Please help me!")
    assert subs.is_english("I love this guy, Japan is a guy")
    assert not subs.is_english("ちょっと待って")
    assert not subs.is_english("OK")              # 1語だけは出さない
    assert not subs.is_english("ARCで外に出る")


def test_mark_title():
    t = "野良外国人に命乞いされる加藤純一【2026/11/04】"
    assert subs.mark_title(t, 5) == "【日本語字幕付き】野良外国人に命乞いされる加藤純一【2026/11/04】"
    assert subs.mark_title(t, 4) == t
    assert subs.mark_title(subs.mark_title(t, 9), 9).count("日本語字幕付き") == 1


def test_events_are_relative_to_clip():
    ev = subs.events([(Line(105.0, 105.5, "help"), "助けて")], 100.0)
    assert ev[0].startswith("Dialogue: 3,0:00:05.00,0:00:06.20,Sub,")
    assert ev[0].endswith("助けて")


def test_events_not_shown_too_long_or_overlapping():
    ev = subs.events([(Line(110.0, 154.0, "thank you so much"), "本当にありがとう！"),
                      (Line(112.0, 115.0, "go"), "行こう")], 100.0)
    assert ev[0].startswith("Dialogue: 3,0:00:10.00,0:00:12.00,")   # 次の字幕の頭で切る
    assert subs.shown_sec("本当にありがとう！") < 6


def test_merge_lines_joins_fragments():
    ls = [Line(10.0, 11.0, "that you at least have somebody"), Line(11.2, 12.0, "who lived there, man"),
          Line(12.1, 13.0, "Oh, I'm sorry."), Line(13.1, 14.0, "Let's go"), Line(20.0, 21.0, "Here")]
    out = subs.merge_lines(ls)
    assert [x.text for x in out] == ["that you at least have somebody who lived there, man Oh, I'm sorry.",
                                     "Let's go", "Here"]


def test_wrap_long_translation():
    w = subs.wrap("戻ってきた。Katoが後ろから来て、俺は手を下げてるところを撃たれた")
    assert len(w) == 2 and all(len(x) <= subs.LINE_MAX for x in w)
    assert subs.wrap("行こう") == ["行こう"]
