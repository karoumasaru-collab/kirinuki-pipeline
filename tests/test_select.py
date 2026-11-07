from pathlib import Path

from kirinuki.chat import VideoInfo
from kirinuki.peaks import Candidate
from kirinuki.select import (
    SCHEMA, Crop, build_content, choose_shorts, clamp_crop, fallback_selection, normalize_all,
    normalize_clip,
)
from kirinuki.transcribe import Line

CAND = Candidate(3, 1000.0, 955.0, 1015.0, 9.5, (("草", 10), ("キチガイ", 2)))
FETCHED = (950.0, 1020.0)
VIDEO = VideoInfo("123", "テスト配信", "2026-10-25T12:00:00Z", 3600, "Overwatch")


def raw(**kw):
    base = {"cand_id": 3, "keep": True, "reason": "オチがある", "kind": "short", "in_sec": 960, "out_sec": 1010,
            "title": "タイトル", "band": ["赤い行", "白い行"], "thumb_text": "神回",
            "crop": {"mode": "face", "cx": 0.9, "cy": 0.8, "size": 0.4}, "hook": "h", "quality": 8}
    base.update(kw)
    return base


def test_schema_requires_every_field():
    item = SCHEMA["properties"]["clips"]["items"]
    assert set(item["required"]) == set(item["properties"])


def test_normalize_clamps_scene_to_fetched_range_and_short_to_60s():
    s = normalize_clip(raw(in_sec=900, out_sec=2000), CAND, FETCHED, short_max=60)
    assert (s.in_sec, s.out_sec) == (950.0, 1020.0)  # 場面は取得済みの範囲に収める
    assert s.short_range() == (950.0, 1010.0)         # ショートは60秒まで


def test_short_range_inside_scene():
    s = normalize_clip(raw(in_sec=955, out_sec=1015, short_in_sec=970, short_out_sec=1015), CAND, FETCHED, 60)
    assert s.short_range() == (970.0, 1015.0)
    s = normalize_clip(raw(in_sec=955, out_sec=1015, short_in_sec=2000, short_out_sec=3000), CAND, FETCHED, 60)
    assert s.short_range() == (955.0, 1015.0)  # 場面の外 → 場面の終わりから60秒以内


def test_normalize_digest_filters_orders_and_caps():
    from kirinuki.select import normalize_digest
    c2 = Candidate(4, 2000.0, 1955.0, 2015.0, 20.0, ())
    c3 = Candidate(5, 3000.0, 2955.0, 3015.0, 5.0, ())
    a = normalize_clip(raw(kind="both"), CAND, FETCHED, 60)                                   # 50秒
    b = normalize_clip(raw(cand_id=4, kind="digest", in_sec=1960, out_sec=2010), c2, (1950, 2020), 60)
    n = normalize_clip(raw(cand_id=5, kind="short", in_sec=2960, out_sec=3010), c3, (2950, 3020), 60)
    d = normalize_digest({"title": "速報", "order": [4, 3, 5, 4, 99], "description": "説明", "thumb_text": "神回",
                          "chapters": [{"cand_id": 4, "label": "チャプター名がとても長い場合は切られます"}]},
                         [a, b, n], max_sec=900)
    assert [i.sel.cand_id for i in d.items] == [4, 3, 5]   # 重複・不明は除く
    assert d.items[0].label == "チャプター名がとても長い場合は切"[:15]
    assert d.items[1].label == "タイトル"               # ラベルが無ければタイトル
    assert d.total_sec == 150
    assert normalize_digest({"order": [4]}, [a, b], 900) is None  # 1場面だけなら作らない
    capped = normalize_digest({"order": [3, 4]}, [a, b], max_sec=60)
    assert capped is None                                # 上限で1場面しか入らない


def test_normalize_too_short_falls_back_to_candidate_window():
    s = normalize_clip(raw(in_sec=1000, out_sec=1002), CAND, FETCHED, short_max=60)
    assert (s.in_sec, s.out_sec) == (CAND.start_sec, CAND.end_sec)


def test_normalize_truncates_texts():
    s = normalize_clip(raw(title="あ" * 50, band=["い" * 15, "We are family"], thumb_text="う" * 20), CAND, FETCHED, 60)
    assert len(s.title) == 40 and s.band == ("い" * 10, "We are family") and len(s.thumb_text) == 10


def test_keep_false_or_none_kind_is_rejected():
    assert not normalize_clip(raw(kind="none"), CAND, FETCHED, 60).keep
    s = normalize_clip(raw(keep=False), CAND, FETCHED, 60)
    assert not s.keep and s.kind == "none"


def test_both_mode_has_pip_and_defaults_when_missing():
    s = normalize_clip(raw(crop={"mode": "both", "cx": 0.5, "cy": 0.5, "size": 1.0,
                                 "fx": 0.9, "fy": 0.8, "fsize": 0.3}), CAND, FETCHED, 60)
    assert s.crop.pip() is not None and s.crop.main() == (0.5, 0.5, 1.0)
    d = clamp_crop(Crop("both", 0.5, 0.5, 1.0))  # 小窓の指定なし → 右下の既定値
    assert d.fsize == 0.35 and d.pip() is not None
    assert clamp_crop(Crop("game", 0.5, 0.5, 1.0)).pip() is None


def test_missing_mode_defaults_to_both():
    r = raw(); r["crop"] = {}
    assert normalize_clip(r, CAND, FETCHED, 60).crop.mode == "both"


def test_clamp_crop_keeps_square_inside_16x9():
    c = clamp_crop(Crop("face", 0.99, 0.99, 0.4))
    half_w = 0.4 / (16 / 9) / 2
    assert abs(c.cx - (1 - half_w)) < 1e-9 and abs(c.cy - 0.8) < 1e-9
    c = clamp_crop(Crop("weird", 0.5, 0.5, 5.0))
    assert c.mode == "game" and c.size == 1.0 and c.cy == 0.5


def test_normalize_all_ignores_unknown_and_duplicate_ids():
    sels = normalize_all({"clips": [raw(), raw(cand_id=99), raw(title="2回目")]}, [CAND], {3: FETCHED}, 60)
    assert len(sels) == 1 and sels[0].title == "タイトル"


def test_choose_shorts_by_quality_then_time_order():
    c2 = Candidate(4, 2000.0, 1955.0, 2015.0, 20.0, ())
    s1 = normalize_clip(raw(quality=5), CAND, FETCHED, 60)
    s2 = normalize_clip(raw(cand_id=4, in_sec=1960, out_sec=2010, quality=9), c2, (1950, 2020), 60)
    s3 = normalize_clip(raw(kind="digest"), CAND, FETCHED, 60)
    assert [s.cand_id for s in choose_shorts([s1, s2, s3], [CAND, c2], 1)] == [4]
    assert [s.cand_id for s in choose_shorts([s2, s1], [CAND, c2], 5)] == [3, 4]


def test_fallback_selection_takes_top_scores():
    c2 = Candidate(4, 2000.0, 1955.0, 2015.0, 20.0, ())
    sels = fallback_selection([CAND, c2], VIDEO, 1)
    assert [s.keep for s in sels] == [False, True]
    assert not sels[1].by_claude and sels[1].crop.mode == "game"


def test_build_content_interleaves_text_and_images_and_hides_ng(tmp_path: Path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    tpl = "配信：{stream_title}／{game_name}／{vod_url}\n{candidates}\nおわり"
    blocks = build_content(tpl, VIDEO, [CAND], {3: FETCHED}, {3: [Line(960, 962, "やったー")]},
                           {3: img}, ("きちがい",))
    assert [b["type"] for b in blocks] == ["text", "text", "image", "text"]
    assert "テスト配信／Overwatch／https://www.twitch.tv/videos/123" in blocks[0]["text"]
    assert "やったー" in blocks[1]["text"] and "草×10" in blocks[1]["text"]
    assert "キチガイ" not in blocks[1]["text"]
    assert blocks[-1]["text"].strip() == "おわり"


def test_thumb_time_prefers_claude_then_peak_then_middle():
    from kirinuki.pipeline import thumb_time
    s = normalize_clip(raw(thumb_sec=990), CAND, FETCHED, 60)
    assert thumb_time(s, 1000) == 990
    s = normalize_clip(raw(thumb_sec=5000), CAND, FETCHED, 60)  # 区間外
    assert thumb_time(s, 1000) == 1000
    assert thumb_time(s, 4000) == (s.in_sec + s.out_sec) / 2


def _mk(cid, a, b, q=5, kind="both"):
    c = Candidate(cid, a + 45, a, b, 1.0, ())
    return normalize_clip(raw(cand_id=cid, kind=kind, in_sec=a, out_sec=b, quality=q), c, (a - 5, b + 5), 60), c


def test_normalize_digests_no_overlap_and_short_cap():
    from kirinuki.select import normalize_digests
    pairs = [_mk(i, i * 100, i * 100 + 60) for i in range(1, 8)]
    sels = [s for s, _ in pairs]
    raws = [{"title": "A", "order": [1, 2, 3, 4], "description": "", "thumb_text": "", "chapters": []},
            {"title": "B", "order": [3, 5, 6, 7], "description": "", "thumb_text": "", "chapters": []},
            {"title": "C", "order": [1, 2], "description": "", "thumb_text": "", "chapters": []},
            {"title": "D", "order": [6, 7], "description": "", "thumb_text": "", "chapters": []}]
    ds = normalize_digests(raws, sels, short_ids={1, 2, 3}, max_sec=1500, max_count=3, max_short_scenes=2)
    assert [d.title for d in ds] == ["A", "B"]             # Cは全部使用済み、Dは上限3本の外
    assert [i.sel.cand_id for i in ds[0].items] == [1, 2, 4]  # ショートの場面は2つまで
    assert [i.sel.cand_id for i in ds[1].items] == [3, 5, 6, 7]  # 3はAで使われていない
    assert normalize_digests({"title": "old", "order": [1, 2], "chapters": []}, sels, set(), 1500, 3, 2)[0].title == "old"


def test_choose_shorts_by_quality_with_minimum():
    pairs = [_mk(i, i * 100, i * 100 + 60, q) for i, q in [(1, 9), (2, 8), (3, 5), (4, 4), (5, 7)]]
    sels, cands = [s for s, _ in pairs], [c for _, c in pairs]
    assert [s.cand_id for s in choose_shorts(sels, cands, 8, 3, 7)] == [1, 2, 5]
    assert [s.cand_id for s in choose_shorts(sels, cands, 2, 3, 7)] == [1, 2]
    low = [_mk(i, i * 100, i * 100 + 60, 5)[0] for i in range(1, 6)]
    assert len(choose_shorts(low, cands, 8, 3, 7)) == 3    # 7以上が無くても3本は作る
