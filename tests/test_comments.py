from kirinuki.chat import ChatMessage, Fragment
from kirinuki.comments import (
    Layout, assign_lanes, build_ass, clip_comments, filter_comment, load_ng_words,
    normalize_for_ng, text_width,
)
from kirinuki.config import CommentsConfig
from kirinuki.overlay import ass_time as _ass_time

CFG = CommentsConfig(max_chars=30)
NG = ("きちがい", "しね")


def msg(body, t=0.0, fragments=()):
    return ChatMessage(offset_sec=t, body=body, fragments=tuple(fragments))


def test_normalize_for_ng_unifies_kana_width_and_separators():
    assert normalize_for_ng("キ チ・ガ イ") == "きちがい"
    assert normalize_for_ng("ＡＢＣ") == "abc"
    assert normalize_for_ng("死-ね") == "死ね"


def test_load_ng_words_skips_comments_and_blank(tmp_path):
    p = tmp_path / "ng.txt"
    p.write_text("# 見出し\n\nキチガイ\n 死ね \n", encoding="utf-8")
    assert load_ng_words(p) == ("きちがい", "死ね")


def test_real_ng_list_keeps_common_phrases():
    from kirinuki.config import load_config
    ng = load_ng_words(load_config().ng_words_path())
    for ok in ["うんこちゃん", "考えろ", "それはないしね", "フェラーリ", "敵を殺す", "しねえよ"]:
        assert filter_comment(msg(ok), ng, CFG) == ok, ok
    for bad in ["ｷﾁｶﾞｲ", "死 ね", "ぶっ殺すぞ"]:
        assert filter_comment(msg(bad), ng, CFG) is None, bad


def test_filter_drops_ng_in_any_kana_form():
    assert filter_comment(msg("キチガイだろ"), NG, CFG) is None
    assert filter_comment(msg("ｷﾁｶﾞｲ"), NG, CFG) is None  # 半角カナ
    assert filter_comment(msg("し ね"), NG, CFG) is None


def test_filter_emotes():
    kappa = Fragment("Kappa", True)
    assert filter_comment(msg("Kappa", fragments=[kappa]), NG, CFG) is None
    mixed = msg("Kappa 草", fragments=[kappa, Fragment(" 草", False)])
    assert filter_comment(mixed, NG, CFG) == "草"


def test_filter_urls_replies_long_and_personal_info():
    assert filter_comment(msg("見て https://example.com"), NG, CFG) is None
    assert filter_comment(msg("youtube.com/watch"), NG, CFG) is None
    assert filter_comment(msg("@someone それな"), NG, CFG) is None
    assert filter_comment(msg("あ" * 31), NG, CFG) is None
    assert filter_comment(msg("090-1234-5678"), NG, CFG) is None
    assert filter_comment(msg("連絡は foo@example.jp"), NG, CFG) is None


def test_filter_strips_emoji():
    assert filter_comment(msg("草🧔🍷"), NG, CFG) == "草"
    assert filter_comment(msg("👍🏻❤️"), NG, CFG) is None


def test_filter_passes_normal_comments():
    assert filter_comment(msg("  草  "), NG, CFG) == "草"
    assert filter_comment(msg("12345点"), NG, CFG) == "12345点"
    assert filter_comment(msg("0721"), NG, CFG) == "0721"


def test_text_width():
    assert text_width("草", 50) == 50
    assert text_width("ab", 50) == 60


def test_assign_lanes_never_overlaps_or_catches_up():
    W, D, FS = 1080, 4.0, 56
    items = [(i * 0.2, "あ" * (1 + i % 12)) for i in range(200)]
    flows = assign_lanes(items, W, D, n_lanes=6, font_size=FS)
    assert 0 < len(flows) < len(items)  # 混んでいるので一部は捨てる
    by_lane = {}
    for f in flows:
        by_lane.setdefault(f.lane, []).append(f)
    for lane in by_lane.values():
        for p, q in zip(lane, lane[1:]):
            vp, vq = (W + p.width) / D, (W + q.width) / D
            # q が出る時点で p の末尾は画面に入りきっている
            assert q.start_sec >= p.start_sec + p.width / vp - 1e-9
            # q の先頭が左端に着くのは p が消えた後
            assert q.start_sec + W / vq >= p.start_sec + D - 1e-9


def test_assign_lanes_uses_first_free_lane():
    flows = assign_lanes([(0, "あ"), (0, "い"), (10, "う")], 1080, 4, n_lanes=2, font_size=56)
    assert [f.lane for f in flows] == [0, 1, 0]


def test_ass_time_and_escape():
    assert _ass_time(3725.456) == "1:02:05.46"
    layout = Layout(1080, 1920, 656, 486, 56, 8)
    flows = assign_lanes([(1.0, "a{b}\\c")], 1080, 4, layout.n_lanes, 56)
    ass = build_ass(flows, layout, "Noto Sans CJK JP", 4)
    assert "a｛b｝＼c" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:05.00,Flow" in ass
    assert "\\move(1080," in ass


def test_clip_comments_relative_times_and_counts():
    msgs = [msg("前", 99.0), msg("草", 100.5), msg("キチガイ", 101.0), msg("後", 160.0)]
    layout = Layout(1080, 1920, 656, 486, 56, 8)
    cc = clip_comments(msgs, 100.0, 160.0, NG, CFG, layout)
    assert (cc.total, cc.passed, cc.shown) == (2, 1, 1)
    assert "0:00:00.50" in cc.ass
    assert "キチガイ" not in cc.ass
