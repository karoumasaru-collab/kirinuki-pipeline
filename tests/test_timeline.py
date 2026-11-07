from kirinuki.chat import ChatMessage, GameChapter, VideoInfo
from kirinuki.timeline import minute_lines


def m(t, body):
    return ChatMessage(t, body, ())


def test_minute_lines_counts_top_and_game_changes():
    v = VideoInfo("1", "t", "", 179, "A", (GameChapter(0, 120, "A"), GameChapter(120, 999, "B")))
    msgs = [m(1, "草"), m(2, "草"), m(3, "ｗｗｗ"), m(4, "www"), m(5, "キチガイ"), m(6, "キチガイ"),
            m(70, "やあ"), m(130, "神"), m(131, "神"), m(132, "とても長いコメントで十六文字を超えるもの")]
    lines = minute_lines(msgs, v, ("きちがい",))
    assert lines[0] == "[0:00:00] 6件 【A】｜草×2　ｗｗｗ×2"
    assert lines[1] == "[0:01:00] 1件"          # 1回だけのコメントは出さない・ゲームが同じなら書かない
    assert lines[2] == "[0:02:00] 3件 【B】｜神×2"
