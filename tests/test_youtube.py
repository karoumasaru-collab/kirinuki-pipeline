from kirinuki.chat import GameChapter, VideoInfo, parse_chat
from kirinuki.config import YouTubeConfig, load_config
from kirinuki.publish_youtube import hashtag, short_description, short_title, upload_md
from kirinuki.report import ShortResult

CFG = YouTubeConfig(enabled=True, title_suffix="【加藤純一】", tags=("加藤純一", "うんこちゃん", "切り抜き"))
VIDEO = VideoInfo("2911434035", "寝る前にOWやる", "2026-10-26T16:00:00Z", 17157, "Overwatch",
                  (GameChapter(0, 5000, "Overwatch"), GameChapter(5000, 17157, "ARC Raiders")))


def test_game_at_uses_twitch_chapters():
    assert VIDEO.game_at(4402) == "Overwatch"
    assert VIDEO.game_at(9656) == "ARC Raiders"
    assert VIDEO.game_at(99999) == "Overwatch"  # 範囲外は配信全体のゲーム


def test_parse_chat_reads_chapters():
    raw = {"video": {"id": "1", "length": 100, "game": "G",
                     "chapters": [{"startMilliseconds": 0, "lengthMilliseconds": 50000, "gameDisplayName": "A"},
                                  {"startMilliseconds": 50000, "lengthMilliseconds": 50000, "gameDisplayName": "B"}]},
           "comments": []}
    v = parse_chat(raw).video
    assert v.game_at(10) == "A" and v.game_at(60) == "B"


def test_hashtag_removes_spaces():
    assert hashtag("ARC Raiders") == "#ARCRaiders"


def test_short_title_appends_suffix_and_strips_angle_brackets():
    assert short_title("ゴッドオブアーク<神>", CFG) == "ゴッドオブアーク＜神＞【加藤純一】"
    assert len(short_title("あ" * 200, CFG)) == 100


def test_short_description_has_source_link_time_and_tags():
    d = short_description(VIDEO, 9656, CFG)
    assert "▼元配信（2026/10/27「寝る前にOWやる」）" in d
    assert "https://www.twitch.tv/videos/2911434035?t=2h40m56s" in d
    assert "非公式の切り抜きチャンネル" in d
    assert d.endswith("#加藤純一 #うんこちゃん #切り抜き #ARCRaiders")


def test_upload_md_marks_uploaded():
    shorts = [ShortResult(11, "shorts/short_02.mp4", 9656, 9688, 75, 75, title="ゴッドオブアークって呼んでくんない？")]
    md = upload_md(VIDEO, shorts, CFG, {"shorts/short_02.mp4": "abc123"})
    assert "https://studio.youtube.com/video/abc123/edit" in md
    assert "ゴッドオブアークって呼んでくんない？【加藤純一】" in md


def test_real_config_has_private_friendly_youtube_settings():
    yt = load_config().youtube
    assert yt.title_suffix == "【加藤純一】" and yt.category_id == 20 and "加藤純一" in yt.tags


def test_parse_upload_md_roundtrip():
    from kirinuki.publish_youtube import is_limit_error, parse_upload_md
    shorts = [ShortResult(11, "shorts/short_02.mp4", 9656, 9688, 75, 75, title="ゴッドオブアーク")]
    md = upload_md(VIDEO, shorts, CFG, {}, ("digest.mp4", "速報タイトル", "説明\n▼チャプター\n00:00 a", "thumbs/digest.jpg"))
    got = parse_upload_md(md)
    assert got["digest.mp4"] == ("速報タイトル", "説明\n▼チャプター\n00:00 a")
    assert got["shorts/short_02.mp4"][0] == "ゴッドオブアーク【加藤純一】"
    assert "▼元配信" in got["shorts/short_02.mp4"][1]
    assert is_limit_error('"reason": "uploadLimitExceeded"') and not is_limit_error("500")


def test_parse_upload_games_and_playlist_title():
    from kirinuki.publish_youtube import parse_upload_games, parse_upload_md, playlist_title
    md = ("# YouTube用テキスト：x\n\n## topic_01.mp4（topic）\n\nYouTube：未アップロード\n\nゲーム：ARC Raiders\n\n"
          "タイトル：\n```\nT1\n```\n概要欄：\n```\nD1\n```\n\n## shorts/short_01.mp4（short）\n\nYouTube：未アップロード\n\n"
          "タイトル：\n```\nT2\n```\n概要欄：\n```\nD2\n```\n")
    assert parse_upload_games(md) == {"topic_01.mp4": "ARC Raiders"}
    assert parse_upload_md(md)["topic_01.mp4"] == ("T1", "D1")
    assert playlist_title("ARC Raiders") == "ARC Raiders｜加藤純一 切り抜き"
    assert playlist_title("") == "雑談｜加藤純一 切り抜き"
