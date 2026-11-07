from datetime import datetime, timedelta, timezone

from kirinuki.state import is_auth_error, needs_processing, pending, record
from kirinuki.twitch import Archive, parse_archive, parse_duration

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
AGE = timedelta(hours=48)


def arc(vid, ended_ago_min, dur_h=3):
    created = NOW - timedelta(minutes=ended_ago_min) - timedelta(hours=dur_h)
    return Archive(vid, f"t{vid}", created, dur_h * 3600)


def test_parse_duration_and_archive():
    assert parse_duration("3h8m33s") == 11313 and parse_duration("45s") == 45
    a = parse_archive({"id": 1, "title": "x", "created_at": "2026-10-27T10:00:00Z", "duration": "1h0m0s"})
    assert a.vod_id == "1" and a.ended_at.hour == 11


def test_pending_skips_live_recent_old_and_done():
    archives = [arc("new", 30), arc("fresh", 3), arc("old", 60 * 72), arc("done", 60)]
    st = {"done": {"status": "done"}}
    assert [a.vod_id for a in pending(archives, st, NOW, live=False, max_age=AGE)] == ["new"]
    assert pending(archives, st, NOW, live=True, max_age=AGE) == []


def test_pending_orders_newest_first():
    got = pending([arc("a", 300), arc("b", 30)], {}, NOW, False, AGE)
    assert [a.vod_id for a in got] == ["b", "a"]


def test_failed_retries_up_to_three_times():
    st = {}
    for _ in range(2):
        record(st, "v", "failed", NOW, error="x")
    assert st["v"]["attempts"] == 2 and needs_processing(st["v"])
    record(st, "v", "failed", NOW)
    assert not needs_processing(st["v"])
    record(st, "v", "done", NOW, youtube_ids=["a"])
    assert st["v"]["status"] == "done" and st["v"]["youtube_ids"] == ["a"]


def test_auth_error_detection():
    assert is_auth_error("HTTP Error 403: Forbidden (subscriber only)")
    assert not is_auth_error("ffmpeg exited with code 1")


def test_upload_log_counts_last_24h(tmp_path):
    from kirinuki.state import add_uploads, recent_uploads
    p = tmp_path / "u.json"
    add_uploads(3, NOW - timedelta(hours=30), p)
    add_uploads(2, NOW - timedelta(hours=1), p)
    assert recent_uploads(NOW, path=p) == 2
    add_uploads(1, NOW + timedelta(days=3), p)   # 2日より古い記録は消える
    assert recent_uploads(NOW + timedelta(days=3), path=p) == 1


def test_short_budget_is_separate(tmp_path):
    from kirinuki.state import record_uploads, upload_budget
    p, sp = tmp_path / "u.json", tmp_path / "s.json"
    record_uploads(["topic_01.mp4", "topic_02.mp4", "shorts/short_01.mp4"], NOW - timedelta(hours=1), p, sp)
    assert upload_budget(4, 2, NOW, p, sp) == {"long": 2, "short": 1}
