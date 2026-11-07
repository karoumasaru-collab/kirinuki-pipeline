"""英語の会話の日本語字幕（DESIGN.md 9.6）。日本語の発言には字幕を付けない。"""

from __future__ import annotations

import re

from kirinuki import overlay
from kirinuki.fetch import Segment
from kirinuki.transcribe import Line, load_audio

MODEL = "claude-haiku-5-5"    # 短い英語を訳すだけなので安いモデル
WHISPER_MODEL = "small"        # 英語の聞き取りは判断材料用（base）より正確なものを使う（2026-11-05 オーナー了承）
MIN_LINES_FOR_TITLE = 5        # これ以上字幕が入ったらタイトルに【日本語字幕付き】
TITLE_MARK = "【日本語字幕付き】"   # タイトルの先頭に付ける（2026-11-06 オーナー判断）
THUMB_BADGE = "日本語字幕付き"    # サムネの右上にも入れる
SIZE = 60                      # 1920x1080 の画面での字幕の大きさ
MARGIN_V = 36

PROMPT = """加藤純一さんのゲーム配信（{game}）で聞こえた英語の会話を、日本語の字幕にしてください。
相手は海外のプレイヤーのことが多く、加藤さん本人が英語で話している部分もあります。

- 話し言葉のまま、短く自然な日本語にする（1行20字くらいまで）。
- 固有名詞・ゲーム用語（アイテム名・場所の名前など）は無理に訳さず、カタカナか英語のままでよい。
- 聞き取りの誤りで意味が通らない行は、ja を空にする（字幕を出さない）。推測で話を作らない。
- 番号（i）はそのまま返す。

{lines}"""

SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "ja": {"type": "string"}},
        "required": ["i", "ja"], "additionalProperties": False}}},
    "required": ["lines"], "additionalProperties": False,
}


def is_english(text: str) -> bool:
    """ローマ字が中心で、2語以上ある行を英語とみなす（区切りごとの言語判定の結果は文字で見分ける）。"""
    letters = sum(1 for c in text if c.isascii() and c.isalpha())
    others = sum(1 for c in text if not c.isascii() and not c.isspace())
    return letters >= 4 and letters > 3 * others and len(re.findall(r"[A-Za-z']+", text)) >= 2


def english_lines(seg: Segment, start: float, end: float, model: str, compute_type: str) -> list[Line]:
    """区間の音声を、区切りごとに言語を判定しながら文字起こしし、英語の行だけ返す（元配信の秒）。"""
    from faster_whisper import WhisperModel   # 重いので使うときだけ読み込む
    w = WhisperModel(model, device="cpu", compute_type=compute_type)
    segments, _ = w.transcribe(load_audio(seg.path), multilingual=True, vad_filter=True, beam_size=1)
    out = []
    for s in segments:
        a, b, text = seg.start_sec + s.start, seg.start_sec + s.end, s.text.strip()
        if start <= a < end and is_english(text):
            out.append(Line(a, min(b, end), text))
    return out


def merge_lines(lines: list[Line], gap: float = 0.8, max_sec: float = 7.0, max_words: int = 16) -> list[Line]:
    """文字起こしが文の途中で細かく切れるので、続いている区切りを1文にまとめる（訳が切れ切れにならないように）。"""
    out: list[Line] = []
    for x in lines:
        p = out[-1] if out else None
        if (p and x.start_sec - p.end_sec <= gap and not p.text.rstrip().endswith((".", "?", "!"))
                and x.end_sec - p.start_sec <= max_sec and len((p.text + " " + x.text).split()) <= max_words):
            out[-1] = Line(p.start_sec, x.end_sec, f"{p.text} {x.text}")
        else:
            out.append(x)
    return out


def translate(lines: list[Line], game: str) -> list[tuple[Line, str]]:
    """英語の行を日本語にする。訳せなかった行（空）は外す。"""
    if not lines:
        return []
    from kirinuki import select
    body = "\n".join(f"{i}: {x.text}" for i, x in enumerate(lines))
    raw, _ = select.call_claude(MODEL, [{"type": "text", "text": PROMPT.format(game=game or "ゲーム", lines=body)}],
                                SCHEMA)
    ja = {int(r["i"]): str(r["ja"]).strip() for r in raw.get("lines", [])}
    return [(x, ja[i]) for i, x in enumerate(lines) if ja.get(i)]


def style() -> str:
    """画面の下・中央に、白い文字と黒い縁（流れるコメントは上3/4なので重ならない）。"""
    return ("Style: Sub," + overlay.TITLE_FONT + f",{SIZE},{overlay.WHITE},{overlay.WHITE},{overlay.BLACK},"
            f"&H80000000,-1,0,0,0,100,100,0,0,1,5,0,2,60,60,{MARGIN_V},1")


LINE_MAX = 26   # 1行に入る字数（1920幅・60px。画面の自動の折り返しは切ってあるので、自分で折る）


def wrap(ja: str) -> list[str]:
    """長い訳を2行に折る。句読点・空白の近くで、なるべく真ん中で。"""
    if len(ja) <= LINE_MAX:
        return [ja]
    mid = len(ja) / 2
    cuts = [i + 1 for i, c in enumerate(ja[:-1]) if c in "、。，,！？!? 　…"]
    i = min(cuts, key=lambda k: abs(k - mid)) if cuts else round(mid)
    if abs(i - mid) > len(ja) / 4:
        i = round(mid)
    return [ja[:i].strip(), ja[i:].strip()]


def shown_sec(ja: str) -> float:
    """字幕を出しておく長さ。読める長さだけ（文字起こしの区切りが長くても出しっぱなしにしない）。"""
    return min(6.0, max(1.5, 1.0 + 0.2 * len(ja)))


def events(subs: list[tuple[Line, str]], clip_start: float) -> list[str]:
    ev = []
    for k, (x, ja) in enumerate(subs):
        end = min(max(x.end_sec, x.start_sec + 1.2), x.start_sec + shown_sec(ja))
        if k + 1 < len(subs):
            end = min(end, max(subs[k + 1][0].start_sec, x.start_sec + 1.0))   # 次の字幕と重ねない
        a, b = x.start_sec - clip_start, end - clip_start
        text = "\\N".join(overlay.ass_escape(x) for x in wrap(ja))
        ev.append(f"Dialogue: 3,{overlay.ass_time(a)},{overlay.ass_time(b)},Sub,,0,0,0,,{text}")
    return ev


def mark_title(title: str, n_subs: int) -> str:
    """字幕が多い動画は、タイトルの先頭に【日本語字幕付き】を付ける。"""
    if n_subs < MIN_LINES_FOR_TITLE or TITLE_MARK in title:
        return title
    return TITLE_MARK + title
