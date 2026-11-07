from kirinuki.chat import GameChapter, VideoInfo
from kirinuki.config import YouTubeConfig
from kirinuki.digest import chapters, description, thumb_ass, timestamp
from kirinuki.publish_youtube import digest_description, digest_title, upload_md
from kirinuki.select import Digest, DigestItem, Selection

VIDEO = VideoInfo("2911434035", "寝る前にOWやる", "2026-10-26T16:00:00Z", 17157, "Overwatch",
                  (GameChapter(0, 5000, "Overwatch"), GameChapter(5000, 17157, "ARC Raiders")))
CFG = YouTubeConfig(enabled=True, title_suffix="【加藤純一】", tags=("加藤純一", "切り抜き"))


def sel(cid, a, b, q=5):
    return Selection(cid, True, "", "both", a, b, "t", ("", ""), "", quality=q)


DG = Digest("ゲージ待て！が神すぎた", (DigestItem(sel(5, 4380, 4420), "ゲージ待て"),
                                   DigestItem(sel(11, 9640, 9700), "ゴッドオブアーク")), "説明文です。", "神回")


def test_timestamp_format():
    assert timestamp(0) == "00:00" and timestamp(65) == "01:05" and timestamp(3725) == "1:02:05"


def test_chapters_start_at_zero_and_merge_short_parts():
    assert chapters([("a", 40), ("b", 60)]) == [(0, "a"), (40, "b")]
    # 10秒未満の区間は前にまとめる（最初は必ず残す）
    assert chapters([("a", 5), ("b", 8), ("c", 30)]) == [(0, "a"), (13, "c")]
    # 最後が10秒未満になるならまとめる
    assert chapters([("a", 30), ("b", 30), ("c", 9)]) == [(0, "a"), (30, "b")]
    assert chapters([("a", 30), ("b", 5)]) == [(0, "a")]


def test_description_has_chapters_and_per_scene_source_links():
    d = description(VIDEO, DG, ["#加藤純一"], "非公式です。")
    assert d.startswith("説明文です。")
    assert "▼チャプター\n00:00 ゲージ待て\n00:40 ゴッドオブアーク" in d
    assert "・ゲージ待て：https://www.twitch.tv/videos/2911434035?t=1h13m0s" in d
    assert "・ゴッドオブアーク：https://www.twitch.tv/videos/2911434035?t=2h40m40s" in d


def test_digest_title_adds_name_only_when_missing():
    assert digest_title("ゲージ待て！が神すぎた", CFG) == "ゲージ待て！が神すぎた【加藤純一】"
    assert digest_title("【加藤純一】ゲージ待て！", CFG) == "【加藤純一】ゲージ待て！"


def test_digest_description_tags_each_game_once():
    d = digest_description(VIDEO, DG, CFG)
    assert d.endswith("#加藤純一 #切り抜き #Overwatch #ARCRaiders")


def test_upload_md_includes_digest_first():
    md = upload_md(VIDEO, [], CFG, {"digest.mp4": "xyz"}, ("digest.mp4", "T", "D", "thumbs/digest.jpg"))
    assert md.index("## digest.mp4（最新速報）") > 0 and "video/xyz/edit" in md


def test_thumb_ass_has_text():
    assert "神回" in thumb_ass("神回") and "PlayResX: 1280" in thumb_ass("神回")


def test_thumb_ass_layout_elements():
    a = thumb_ass("宇宙港で悪魔に覚醒", "ARC Raiders", ("やめんべ", "えらいwww"))
    assert "宇宙港で悪魔に覚醒" in a and "\\pos(36,26)}ARC Raiders" in a
    assert "やめんべ" in a and "えらいwww" in a and "Style: Bubble" in a


def test_thumb_filter_with_face_and_band():
    from kirinuki.digest import thumb_filter
    f = thumb_filter((0.88, 0.75, 0.35), "t.ass")
    assert "drawbox=x=0:y=556:w=1280:h=164:color=0xE62E2E:t=fill" in f
    assert "scale=420:420,drawbox" in f and "overlay=832:118,ass=filename=t.ass[v]" in f
    assert "split" not in thumb_filter(None, "t.ass")


def test_thumb_bubbles_and_face_box():
    from kirinuki.pipeline import face_box, thumb_bubbles
    from kirinuki.select import Crop
    top = (("wwwww", 40), ("草", 9), ("キチガイ", 5), ("とても長いチャットの文章です", 4), ("やめんべ", 3), ("えらい", 2), ("3つ目", 1))
    assert thumb_bubbles(top, ("きちがい",)) == ("やめんべ", "えらい")
    assert face_box(Crop("both", 0.5, 0.5, 1.0, 0.9, 0.8, 0.3)) == (0.9, 0.8, 0.3)
    assert face_box(Crop("face", 0.7, 0.6, 0.4)) == (0.7, 0.6, 0.4)
    assert face_box(Crop("game")) == (0.88, 0.75, 0.35)
