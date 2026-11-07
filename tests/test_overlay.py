from kirinuki.overlay import band_overlay, display_len, fit_size, thumb_overlay, truncate_display
from kirinuki.render import short_video_filter


def test_display_len_and_fit():
    assert display_len("あa") == 1.5
    assert fit_size("ブルックアンチ", 200) == 145  # 1020 / 7
    assert fit_size("あ", 150) == 150


def test_band_two_lines_red_and_white():
    styles, events = band_overlay(("ブルックアンチ", "加藤純一"), 30)
    assert len(styles) == 2 and len(events) == 2
    assert ",Band1," in events[0] and "ブルックアンチ" in events[0]
    assert ",Band2," in events[1] and "0:00:30.00" in events[1]


def test_band_single_line_is_centered():
    _, events = band_overlay(("神回", ""), 10)
    assert len(events) == 1 and "\\pos(540,220)" in events[0] and "\\fs260" in events[0]
    assert band_overlay(("", ""), 10)[1] == []


def test_thumb_overlay():
    _, events = thumb_overlay("神回")
    assert len(events) == 1 and "神回" in events[0]
    assert thumb_overlay("")[1] == []


def test_short_filter_crop_and_layout():
    f = short_video_filter(0.9, 0.8, 0.4, "a.ass")
    assert "crop=w=ih*0.4000:h=ih*0.4000:x=iw*0.9000-ih*0.4000/2:y=ih*0.8000-ih*0.4000/2" in f
    assert "drawbox=x=0:y=0:w=1080:h=440" in f
    assert "overlay=0:440,ass=filename=a.ass[v]" in f


def test_truncate_display_counts_halfwidth_as_half():
    assert truncate_display("We are family!", 10) == "We are family!"[:14]
    assert truncate_display("あいうえおかきくけこさ", 10) == "あいうえおかきくけこ"
    assert truncate_display("あa" * 10, 3) == "あaあa"


def test_short_filter_with_pip():
    f = short_video_filter(0.5, 0.5, 1.0, "a.ass", pip=(0.88, 0.75, 0.35))
    assert "split=3[a][b][c]" in f
    assert "scale=360:360,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=6[pip]" in f
    assert "[base][pip]overlay=696:1136,ass=filename=a.ass[v]" in f
