from kirinuki.pipeline2 import merge_add


def test_merge_add_keeps_old_pending_and_adds_new():
    entry = {"youtube_pending": ["topic_01.mp4", "shorts/short_01.mp4"], "youtube_ids": ["abc"],
             "files": ["topic_01.mp4", "shorts/short_01.mp4"]}
    new = [{"file": "topic_04.mp4", "pending": True, "youtube_id": None},
           {"file": "topic_05.mp4", "pending": False, "youtube_id": "xyz"}]
    m = merge_add(entry, ["shorts/short_01.mp4"], new)
    assert m["youtube_pending"] == ["topic_01.mp4", "topic_04.mp4"]
    assert m["youtube_dropped"] == ["shorts/short_01.mp4"]
    assert m["youtube_ids"] == ["abc", "xyz"]
    assert m["files"] == ["topic_01.mp4", "topic_04.mp4", "topic_05.mp4"]


def test_merge_add_drop_of_uploaded_file_is_not_marked_dropped():
    m = merge_add({"youtube_pending": [], "youtube_dropped": ["x.mp4"]}, ["topic_01.mp4"], [])
    assert m["youtube_pending"] == []
    assert m["youtube_dropped"] == ["x.mp4"]

