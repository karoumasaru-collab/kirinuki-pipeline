from kirinuki.chat import VideoInfo
from kirinuki.pipeline2 import roadshow_description, roadshow_title, split_parts, strip_date
from kirinuki.topics import TopicVideo

V = VideoInfo("9", "日刊ARCレイダース", "2026-10-27T18:00:00Z", 20000, "ARC Raiders")


def tv(i, a, b, game="ARC Raiders", q=5, thumb="", title=None):
    return TopicVideo(i, a, b, title or f"話題{i}する加藤純一【2026/10/28】", thumb, a, None, ((a, "冒頭"),),
                      f"説明{i}", (), q, game)


def test_split_parts_by_two_hours():
    vids = [tv(1, 0, 3000), tv(2, 4000, 7000), tv(3, 8000, 10000)]
    parts = split_parts(vids, 7200)
    assert [[v.topic_id for v in p] for p in parts] == [[1, 2], [3]]


def test_roadshow_title_game_hook_and_parts():
    p1 = [tv(1, 0, 3000, q=9, thumb="野良に裏切られてキレる"), tv(2, 4000, 5000, game="Just Chatting")]
    assert roadshow_title(V, p1, 1, 1) == "加藤純一のARC Raidersダイジェスト　野良に裏切られてキレる【2026/10/28】"
    assert roadshow_title(V, p1, 1, 2).endswith("【2026/10/28前編】")
    assert roadshow_title(V, p1, 2, 2).endswith("【2026/10/28後編】")
    chat = [tv(3, 0, 600, game="Just Chatting", thumb="親友に整形を勧める")]
    assert roadshow_title(V, chat, 1, 1).startswith("加藤純一 雑談ダイジェスト　親友に整形を勧める")


def test_roadshow_description_chapters_and_links():
    d = roadshow_description(V, [tv(1, 100, 700), tv(2, 1000, 1300)], ("加藤純一",))
    assert "00:00 話題1する加藤純一\n10:00 話題2する加藤純一" in d
    assert "・話題2する加藤純一：https://www.twitch.tv/videos/9?t=0h16m40s" in d
    assert d.endswith("#加藤純一 #ARCRaiders")


def test_helpers():
    assert strip_date("鳥にタイマン【2026/10/21】") == "鳥にタイマン"
