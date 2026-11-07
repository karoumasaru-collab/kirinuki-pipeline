from datetime import datetime

from kirinuki.chat import VideoInfo
from kirinuki.peaks import Candidate
from kirinuki.report import JST, ShortResult, build_candidates_md, hms, jst_date, twitch_time_link


def test_hms_and_link():
    assert hms(3725.9) == "1:02:05"
    assert twitch_time_link("123", 3725) == "https://www.twitch.tv/videos/123?t=1h2m5s"


def test_jst_date_crosses_midnight():
    assert jst_date("2026-10-25T16:00:00Z") == "2026-10-26"


def test_candidates_md_marks_shorts_and_hides_ng():
    video = VideoInfo("123", "テスト", "2026-10-25T12:00:00Z", 3600, "ゲーム")
    cands = [
        Candidate(1, 100, 55, 115, 5.0, (("草", 10), ("キチガイ", 3))),
        Candidate(2, 900, 855, 915, 9.0, (("神", 4),)),
    ]
    shorts = [ShortResult(2, "shorts/short_01.mp4", 855, 915, 12, 40)]
    md = build_candidates_md(video, cands, shorts, ("きちがい",), datetime(2026, 10, 27, 9, 0, tzinfo=JST))
    assert md.index("### 1. 0:14:15〜0:15:15　スコア 9.0　✅ shorts/short_01.mp4") < md.index("### 2.")
    assert "?t=0h14m15s" in md
    assert "草×10" in md
    assert "キチガイ" not in md
