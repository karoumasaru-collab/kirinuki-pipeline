from kirinuki.chat import VideoInfo
from kirinuki.peaks import Candidate
from kirinuki.topics import fallback_drafts, normalize_final, normalize_plan, normalize_video, TopicDraft

V = VideoInfo("1", "雑談", "2026-10-27T15:00:00Z", 10000, "ARC Raiders")


def test_normalize_plan_drops_short_overlaps_and_caps():
    raw = {"topics": [
        {"start_sec": 0, "end_sec": 100, "summary": "短い", "quality": 9},
        {"start_sec": 1000, "end_sec": 2000, "summary": "A", "quality": 8},
        {"start_sec": 1500, "end_sec": 2500, "summary": "重なり", "quality": 5},
        {"start_sec": 3000, "end_sec": 12000, "summary": "長い", "quality": 7},
    ]}
    d = normalize_plan(raw, 10000)
    assert [(x.id, x.summary) for x in d] == [(1, "A"), (2, "長い")]
    assert d[1].end_sec == 9000  # 最長100分（6000秒）で切る


def test_normalize_video_title_chapters_face_and_ranges():
    dr = TopicDraft(1, 1000, 2000, "要約", 7)
    raw = {"in_sec": 950, "out_sec": 2500, "title": "鳥にタイマン挑む41歳", "thumb_text": "カマキリにワンパンされる男",
           "thumb_sec": 99999, "face": {"fx": 0.99, "fy": 0.99, "fsize": 0.3},
           "chapters": [{"sec": 1200, "label": "a"}, {"sec": 1205, "label": "近すぎ"}, {"sec": 1500, "label": "b"}],
           "description": "説明", "bubbles": ["それはない", "とても長い吹き出しの文章です", "三つ目"], "quality": 9}
    v = normalize_video(raw, dr, V)
    assert (v.in_sec, v.out_sec) == (950, 2120)              # 区切りから±120秒まで
    assert v.title == "鳥にタイマン挑む41歳【2026/10/28】"      # 配信日（日本時間）を付ける
    assert [c[1] for c in v.chapters] == ["冒頭", "a", "b"]     # 最初は開始時刻、10秒未満は間引く
    assert v.chapters[0][0] == 950
    assert v.thumb_sec == (950 + 2120) / 2                     # 範囲外 → 中央
    assert v.face is not None and v.face[2] == 0.3
    assert len(v.bubbles) == 2 and v.game == "ARC Raiders"
    assert len(v.thumb_text) <= 14


def test_normalize_final_keeps_dropped_drafts():
    drafts = [TopicDraft(1, 0, 600, "A", 5), TopicDraft(2, 1000, 1600, "B", 6)]
    vids = normalize_final({"videos": [{"topic_id": 2, "in_sec": 1000, "out_sec": 1600, "title": "B【2026/10/28】",
                                        "thumb_text": "x", "thumb_sec": 1100, "face": {"fx": 0, "fy": 0, "fsize": 0},
                                        "chapters": [], "description": "", "bubbles": [], "quality": 6}]}, drafts, V)
    assert [v.topic_id for v in vids] == [1, 2] and vids[1].face is None


def test_fallback_drafts_from_peaks():
    cands = [Candidate(1, 1000, 955, 1015, 9.0, ()), Candidate(2, 1200, 1155, 1215, 5.0, ()),
             Candidate(3, 5000, 4955, 5015, 7.0, ())]
    d = fallback_drafts(cands, 10000)
    assert [x.summary for x in d] == ["0:16:40の山場", "1:23:20の山場"]  # 重なる2つ目は除く


# ---------- 無音区間・山場の文字起こし・タイトルの点検 ----------

from kirinuki.topics import (apply_checked, avoid_muted_drafts, avoid_muted_video, clean_span,  # noqa: E402
                             draft_text)
from kirinuki.transcribe import Line  # noqa: E402


def test_clean_span_keeps_longest_unmuted_piece():
    assert clean_span(4200, 4560, [(4320, 4500)]) is None             # 寿司ドーム：6分のうち3分が無音 → 残り2分なので作らない
    assert clean_span(1000, 2000, [(1100, 1200)]) == (1200, 2000)      # 長い方を残す
    assert clean_span(1000, 2000, [(5000, 5100)]) == (1000, 2000)      # 重ならなければそのまま


def test_avoid_muted_drafts_and_video():
    drafts = [TopicDraft(1, 4200, 4560, "寿司", 8), TopicDraft(2, 7560, 8100, "試合", 7)]
    assert [d.id for d in avoid_muted_drafts(drafts, [(4320, 4500)])] == [2]
    v = normalize_video({"in_sec": 1000, "out_sec": 2000, "title": "t", "thumb_sec": 1050,
                         "chapters": [{"sec": 1050, "label": "a"}, {"sec": 1500, "label": "b"}]},
                        TopicDraft(1, 1000, 2000, "s", 5), V)
    w = avoid_muted_video(v, [(990, 1200)])
    assert (w.in_sec, w.out_sec) == (1200, 2000)
    assert [c for c in w.chapters] == [(1200, "冒頭"), (1500, "b")]
    assert w.thumb_sec == 1600                                         # 無音側の瞬間は使わない → 中央


def test_draft_text_includes_peak_transcripts_and_muted():
    c = Candidate(id=3, start_sec=1400, peak_sec=1500, end_sec=1550, score=9.0,
                  top_messages=(("草", 20),))
    text = draft_text(TopicDraft(1, 1000, 2000, "s", 5), [], [], [c], {3: [Line(1490, 1495, "まじかよ")]},
                      [(1900, 1950)])
    assert "まじかよ" in text and "音が消されている区間" in text


def test_apply_checked_replaces_only_bad_titles():
    v1 = normalize_video({"title": "ユリース発言でチャットがざわつく加藤純一"}, TopicDraft(1, 0, 600, "s", 5), V)
    v2 = normalize_video({"title": "よいタイトル"}, TopicDraft(2, 1000, 1600, "s", 5), V)
    raw = {"titles": [{"topic_id": 1, "ok": False, "reason": "中身と合わない", "title": "最終盤で粘る加藤純一"},
                      {"topic_id": 2, "ok": True, "reason": "", "title": "よいタイトル"}]}
    vids, notes = apply_checked(raw, [v1, v2], V)
    assert vids[0].title == "最終盤で粘る加藤純一【2026/10/28】"
    assert vids[1].title == v2.title and len(notes) == 1


def test_fix_words_corrects_known_names():
    from kirinuki.topics import fix_words
    assert fix_words("愛染の能力も") == "藍染の能力も"


def test_prefers_matches_title_when_category_is_wrong():
    from types import SimpleNamespace
    from kirinuki.topics import prefers
    v = SimpleNamespace(game="ARC Raiders", title="【ALGS】ZETAを応援する加藤純一【2026/11/01】")
    assert prefers(v, "apex,algs")
    assert not prefers(v, "bleach")
    assert not prefers(v, "")


def test_rank_prefer_then_normal_then_avoid():
    from types import SimpleNamespace as N
    from kirinuki.topics import rank
    prefer, avoid = ("BLEACH", "ALGS"), ("ARC Raiders",)
    vids = [N(game="ARC Raiders", title="ARCの回", quality=8),
            N(game="ARC Raiders", title="【ALGS】応援", quality=6),
            N(game="Just Chatting", title="雑談", quality=7),
            N(game="ARC Raiders", title="神回", quality=9),
            N(game="BLEACH Rebirth of Souls", title="斬月", quality=5)]
    got = [v.title for v in sorted(vids, key=lambda v: rank(v, prefer, avoid), reverse=True)]
    assert got == ["【ALGS】応援", "斬月", "神回", "雑談", "ARCの回"]


def test_rank_arc_only_stream_keeps_quality_order():
    from types import SimpleNamespace as N
    from kirinuki.topics import rank
    vids = [N(game="ARC Raiders", title=f"t{q}", quality=q) for q in (5, 8, 6)]
    got = [v.quality for v in sorted(vids, key=lambda v: rank(v, ("BLEACH",), ("ARC Raiders",)), reverse=True)]
    assert got == [8, 6, 5]


def test_cap_length_keeps_densest_window():
    from kirinuki.topics import TopicVideo, cap_length
    v = TopicVideo(1, 0.0, 6000.0, "t", "x", 100.0, None, ((0.0, "a"), (4000.0, "b")), "d")
    out = cap_length(v, [(100.0, 1.0), (4000.0, 5.0), (4500.0, 5.0)], 3600)
    assert out.out_sec - out.in_sec == 3600
    assert out.in_sec <= 4000 < 4500 < out.out_sec
    assert out.chapters[0][0] == out.in_sec
    assert out.in_sec <= out.thumb_sec < out.out_sec


def test_cap_length_leaves_short_topics():
    from kirinuki.topics import TopicVideo, cap_length
    v = TopicVideo(1, 0.0, 1800.0, "t", "x", 100.0, None, ((0.0, "a"),), "d")
    assert cap_length(v, [], 3600) is v


def test_game_from_title_overrides_wrong_category():
    from kirinuki.topics import TopicVideo, game_from_title
    v = TopicVideo(1, 0, 60, "【ALGS】ZETAを応援する加藤純一【2026/11/01】", "x", 0, None, (), "d", game="ARC Raiders")
    assert game_from_title(v).game == "Apex Legends"
    w = TopicVideo(1, 0, 60, "【BLEACH】回【2026/11/01】", "x", 0, None, (), "d", game="BLEACH Rebirth of Souls")
    assert game_from_title(w) is w


def test_max_length_shorter_for_avoid():
    from types import SimpleNamespace
    from kirinuki.topics import max_length
    o = SimpleNamespace(topic_max_sec=3600, avoid_max_sec=1200, normal_max_sec=1500,
                        prefer=("ALGS",), avoid=("ARC Raiders",))
    arc = SimpleNamespace(title="宇宙港の悪魔", game="ARC Raiders")
    algs = SimpleNamespace(title="ALGS観戦", game="Apex Legends")
    assert max_length(arc, o) == 1200
    assert max_length(algs, o) == 3600
    assert max_length(SimpleNamespace(title="雑談", game="Just Chatting"), o) == 1500
