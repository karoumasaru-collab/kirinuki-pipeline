"""ASS字幕ファイルの組み立てと、ショート上部の黒帯タイトル・サムネ文字（純粋関数）。"""

from __future__ import annotations

import unicodedata

SHORT_W, SHORT_H = 1080, 1920
BAND_H = 440            # 上の黒帯
VIDEO_TOP = BAND_H      # 中央の映像（1080x1080）の上端
VIDEO_SIZE = 1080
PIP_SIZE = 360          # both モードの右下の小窓
PIP_MARGIN = 24
PIP_X = SHORT_W - PIP_SIZE - PIP_MARGIN
PIP_Y = VIDEO_TOP + VIDEO_SIZE - PIP_SIZE - PIP_MARGIN
TITLE_FONT = "Noto Sans CJK JP Black"

STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding"
)

# ASSの色は &HAABBGGRR
RED = "&H002E2EE6"
WHITE = "&H00FFFFFF"
YELLOW = "&H0000E5FF"
BLACK = "&H00000000"


def style(name: str, font: str, size: int, colour: str, outline: float, bold: bool = True,
          alignment: int = 7) -> str:
    return (f"Style: {name},{font},{size},{colour},{colour},{BLACK},{BLACK},"
            f"{-1 if bold else 0},0,0,0,100,100,0,0,1,{outline},0,{alignment},0,0,0,1")


def ass_time(sec: float) -> str:
    cs = max(0, round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ")


def ass_document(play_w: int, play_h: int, styles: list[str], events: list[str]) -> str:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_w}",
        f"PlayResY: {play_h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        STYLE_FORMAT,
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
    ]
    return "\n".join(lines) + "\n"


def display_len(text: str) -> float:
    """全角1・半角0.5で数えた文字数。"""
    return sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.5 for c in text)


def truncate_display(text: str, max_units: float) -> str:
    """全角1・半角0.5で数えて max_units に収まるよう切る。"""
    out, n = [], 0.0
    for c in text:
        n += display_len(c)
        if n > max_units:
            break
        out.append(c)
    return "".join(out)


def fit_size(text: str, max_size: int, width: float = SHORT_W - 60) -> int:
    """横幅に収まる文字サイズ。"""
    n = max(display_len(text), 1.0)
    return int(min(max_size, width / n))


def static_event(start: float, end: float, style_name: str, x: float, y: float, text: str,
                 size: int | None = None) -> str:
    fs = f"\\fs{size}" if size else ""
    return (f"Dialogue: 1,{ass_time(start)},{ass_time(end)},{style_name},,0,0,0,,"
            f"{{\\an5\\pos({round(x)},{round(y)}){fs}}}{ass_escape(text)}")


def band_overlay(lines: tuple[str, str], duration: float) -> tuple[list[str], list[str]]:
    """黒帯の2行（1行目は赤、2行目は白）。2行目が空なら1行目を中央に大きく出す。"""
    l1, l2 = (lines[0] or "").strip(), (lines[1] or "").strip()
    styles = [style("Band1", TITLE_FONT, 200, RED, 7, alignment=5),
              style("Band2", TITLE_FONT, 170, WHITE, 7, alignment=5)]
    events = []
    if l1 and l2:
        # 1行目を大きく（最大200）、2行目は少し小さく（最大170）。帯の高さ440に2行で収める
        events.append(static_event(0, duration, "Band1", SHORT_W / 2, 125, l1, fit_size(l1, 200)))
        events.append(static_event(0, duration, "Band2", SHORT_W / 2, 330, l2, fit_size(l2, 170)))
    elif l1 or l2:
        text = l1 or l2
        events.append(static_event(0, duration, "Band1" if l1 else "Band2", SHORT_W / 2, BAND_H / 2,
                                   text, fit_size(text, 260)))
    return styles, events


def thumb_overlay(thumb_text: str) -> tuple[list[str], list[str]]:
    """サムネ用の大きな黄色い文字（映像の下寄り、右下のワイプより上に置く）。"""
    text = (thumb_text or "").strip()
    styles = [style("Thumb", TITLE_FONT, 220, YELLOW, 12, alignment=5)]
    if not text:
        return styles, []
    y = PIP_Y - 130
    return styles, [static_event(0, 10, "Thumb", SHORT_W / 2, y, text, fit_size(text, 220))]
