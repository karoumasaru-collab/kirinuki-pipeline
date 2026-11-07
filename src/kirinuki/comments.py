"""視聴者コメントをニコニコ風に流すためのASS字幕を作る（DESIGN.md 3.7.1）。

NGワード判定・段（レーン）割り当て・ASS生成はすべて純粋関数。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from kirinuki import overlay
from kirinuki.chat import ChatMessage
from kirinuki.config import CommentsConfig

URL_RE = re.compile(r"https?://|www\.|\.(com|net|jp|io|tv|be|ly|gg)\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 10〜11桁の数字の並び（区切り記号あり／なし）は電話番号らしいとみなして捨てる
PHONE_RE = re.compile(r"(?<!\d)0\d{1,4}[-(]?\d{1,4}[-)]?\d{3,4}(?!\d)")
# NGワード判定の前に取り除く区切り文字（「死 ね」「死・ね」などのすり抜け対策）
SEPARATORS_RE = re.compile(r"[\s・.,_\-*/|]+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# 絵文字は日本語フォントに無く、libass はカラー絵文字を描けないので取り除く
EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿︎️‍]")


def normalize_for_ng(text: str) -> str:
    """全角半角・大小文字・カタカナひらがなをそろえ、区切り文字を除く。"""
    t = unicodedata.normalize("NFKC", text).lower()
    t = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)
    return SEPARATORS_RE.sub("", t)


def load_ng_words(path: Path) -> tuple[str, ...]:
    words = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(normalize_for_ng(line))
    return tuple(w for w in words if w)


def _looks_like_phone(text: str) -> bool:
    t = unicodedata.normalize("NFKC", text)
    return any(sum(ch.isdigit() for ch in m.group()) >= 10 for m in PHONE_RE.finditer(t))


def filter_comment(msg: ChatMessage, ng_words: tuple[str, ...], cfg: CommentsConfig) -> str | None:
    """流してよければ表示用の文字列を、だめなら None を返す。"""
    if cfg.drop_emote_only and msg.is_emote_only():
        return None
    text = msg.text_without_emotes() if msg.fragments else msg.body
    text = re.sub(r"\s+", " ", EMOJI_RE.sub("", CONTROL_RE.sub("", text))).strip()
    if not text or text.startswith("@"):
        return None
    if cfg.drop_urls and URL_RE.search(text):
        return None
    if EMAIL_RE.search(text) or _looks_like_phone(text):
        return None
    key = normalize_for_ng(text)
    if any(w in key for w in ng_words):
        return None
    if len(text) > cfg.max_chars:
        return None
    return text


def text_width(text: str, font_size: float) -> float:
    """表示幅の見積もり。全角は1文字=font_size、半角は0.6倍。"""
    return sum(font_size if unicodedata.east_asian_width(c) in "WF" else font_size * 0.6 for c in text)


@dataclass(frozen=True)
class FlowComment:
    start_sec: float
    text: str
    lane: int
    width: float


def assign_lanes(
    items: list[tuple[float, str]],
    screen_w: float,
    duration: float,
    n_lanes: int,
    font_size: float,
) -> list[FlowComment]:
    """時刻順のコメントを、前のコメントに重ならず追いつかない段へ割り当てる。空きが無いものは捨てる。"""
    last: list[FlowComment | None] = [None] * n_lanes
    out = []
    for t, text in sorted(items, key=lambda x: x[0]):
        w = text_width(text, font_size)
        speed = (screen_w + w) / duration
        for lane in range(n_lanes):
            p = last[lane]
            if p is not None:
                p_speed = (screen_w + p.width) / duration
                tail_entered = t >= p.start_sec + p.width / p_speed
                no_catch_up = t + screen_w / speed >= p.start_sec + duration
                if not (tail_entered and no_catch_up):
                    continue
            fc = FlowComment(start_sec=t, text=text, lane=lane, width=w)
            last[lane] = fc
            out.append(fc)
            break
    return out


@dataclass(frozen=True)
class Layout:
    play_w: int
    play_h: int
    region_top: float
    region_h: float
    font_size: int
    max_lanes: int

    @property
    def lane_h(self) -> float:
        return self.font_size * 1.15

    @property
    def n_lanes(self) -> int:
        return max(1, min(self.max_lanes, int(self.region_h // self.lane_h)))


def short_layout(cfg: CommentsConfig, pip: bool = False) -> Layout:
    """縦1080x1920。中央の正方形の映像（440〜1520）の上80%に流す。小窓があるときは小窓の上まで。"""
    top = overlay.VIDEO_TOP + 16
    bottom = overlay.PIP_Y - 8 if pip else overlay.VIDEO_TOP + overlay.VIDEO_SIZE * 0.8
    return Layout(overlay.SHORT_W, overlay.SHORT_H, top, bottom - top, cfg.font_size_short, cfg.max_lanes_short)


def digest_layout(cfg: CommentsConfig) -> Layout:
    """横1920x1080。画面の上から3/4に流す。"""
    return Layout(1920, 1080, 8, 1080 * 0.75, cfg.font_size_digest, cfg.max_lanes_digest)


def flow_style(layout: Layout, font: str) -> str:
    return overlay.style("Flow", font, layout.font_size, overlay.WHITE, 3)


def flow_events(comments: list[FlowComment], layout: Layout, duration: float) -> list[str]:
    fs = layout.font_size
    out = []
    for c in comments:
        y = round(layout.region_top + c.lane * layout.lane_h + (layout.lane_h - fs) / 2)
        x1, x2 = layout.play_w, -math.ceil(c.width)
        out.append(
            f"Dialogue: 0,{overlay.ass_time(c.start_sec)},{overlay.ass_time(c.start_sec + duration)},Flow,,0,0,0,,"
            f"{{\\move({x1},{y},{x2},{y})}}{overlay.ass_escape(c.text)}"
        )
    return out


def build_ass(comments: list[FlowComment], layout: Layout, font: str, duration: float,
              extra_styles: list[str] = (), extra_events: list[str] = ()) -> str:
    return overlay.ass_document(
        layout.play_w, layout.play_h,
        [flow_style(layout, font), *extra_styles],
        [*flow_events(comments, layout, duration), *extra_events],
    )


@dataclass(frozen=True)
class ClipComments:
    ass: str
    total: int     # 区間内の全コメント数
    passed: int    # フィルタを通った数
    shown: int     # 段に入って実際に流れる数


def clip_comments(
    messages: list[ChatMessage],
    clip_start: float,
    clip_end: float,
    ng_words: tuple[str, ...],
    cfg: CommentsConfig,
    layout: Layout,
    extra_styles: list[str] = (),
    extra_events: list[str] = (),
) -> ClipComments:
    in_clip = [m for m in messages if clip_start <= m.offset_sec < clip_end]
    items = []
    for m in in_clip:
        text = filter_comment(m, ng_words, cfg)
        if text is not None:
            items.append((m.offset_sec - clip_start, text))
    flows = assign_lanes(items, layout.play_w, cfg.duration_sec, layout.n_lanes, layout.font_size)
    return ClipComments(
        ass=build_ass(flows, layout, cfg.font, cfg.duration_sec, extra_styles, extra_events),
        total=len(in_clip),
        passed=len(items),
        shown=len(flows),
    )
