"""話題ごとの切り分け（DESIGN.md 9）。Claude① で区切りを決め、Claude② で各動画を仕上げる。

入力の組み立てと出力の検証・補正は純粋関数。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, replace
from pathlib import Path

from kirinuki.chat import VideoInfo
from kirinuki.fetch import vod_url
from kirinuki.overlay import truncate_display
from kirinuki.peaks import Candidate
from kirinuki.report import hms, jst_date
from kirinuki.select import clamp, clamp_square
from kirinuki.transcribe import Line, format_lines

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
PLAN_PROMPT = PROMPTS / "plan_topics.md"
FINAL_PROMPT = PROMPTS / "finalize_topics.md"
CHECK_PROMPT = PROMPTS / "check_titles.md"
MIN_TOPIC_SEC = 180.0
MAX_TOPIC_SEC = 6000.0
MAX_TOPICS = 8
ADJUST_SEC = 120.0      # Claude② が開始・終了を動かせる幅
LABEL_MAX = 15
MIN_CHAPTER_GAP = 10.0
PEAK_TRANSCRIPTS = 4     # Claude② に渡す、区間内の山場の文字起こしの数

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "summary": {"type": "string"},
                    "quality": {"type": "integer"},
                },
                "required": ["start_sec", "end_sec", "summary", "quality"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "integer"},
                    "in_sec": {"type": "number"},
                    "out_sec": {"type": "number"},
                    "title": {"type": "string"},
                    "thumb_text": {"type": "string"},
                    "thumb_emph": {"type": "string"},
                    "thumb_sec": {"type": "number"},
                    "face": {
                        "type": "object",
                        "properties": {"fx": {"type": "number"}, "fy": {"type": "number"}, "fsize": {"type": "number"}},
                        "required": ["fx", "fy", "fsize"],
                        "additionalProperties": False,
                    },
                    "chapters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"sec": {"type": "number"}, "label": {"type": "string"}},
                            "required": ["sec", "label"],
                            "additionalProperties": False,
                        },
                    },
                    "description": {"type": "string"},
                    "quality": {"type": "integer"},
                },
                "required": ["topic_id", "in_sec", "out_sec", "title", "thumb_text", "thumb_emph", "thumb_sec", "face",
                             "chapters", "description", "quality"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["videos"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TopicDraft:
    id: int
    start_sec: float
    end_sec: float
    summary: str
    quality: int


@dataclass(frozen=True)
class TopicVideo:
    topic_id: int
    in_sec: float
    out_sec: float
    title: str
    thumb_text: str
    thumb_sec: float
    face: tuple[float, float, float] | None
    chapters: tuple[tuple[float, str], ...]   # (元配信の秒, 名前)
    description: str
    bubbles: tuple[str, ...] = ()
    quality: int = 0
    game: str = ""
    thumb_emph: str = ""     # thumb_text の中で大きくする語（2026-11-05）


# ---------- Claude①：区切り ----------

def plan_content(template: str, video: VideoInfo, timeline: list[str]) -> list[dict]:
    text = (template.replace("{stream_title}", video.title or "不明")
            .replace("{vod_url}", vod_url(video.vod_id))
            .replace("{length}", hms(video.length_sec))
            .replace("{timeline}", "\n".join(timeline)))
    return [{"type": "text", "text": text}]


def normalize_plan(raw: dict, length_sec: float) -> list[TopicDraft]:
    """短すぎ・長すぎ・重なりを除き、面白い順に最大8本。id は時刻順に1から。"""
    items = []
    for t in raw.get("topics", []):
        s = clamp(float(t.get("start_sec", 0)), 0, length_sec)
        e = clamp(float(t.get("end_sec", 0)), 0, length_sec)
        if e - s < MIN_TOPIC_SEC:
            continue
        if e - s > MAX_TOPIC_SEC:
            e = s + MAX_TOPIC_SEC
        items.append((s, e, str(t.get("summary", "")).strip(), int(t.get("quality", 0))))
    kept: list[tuple] = []
    for it in sorted(items, key=lambda x: -x[3]):
        if all(it[1] <= k[0] or it[0] >= k[1] for k in kept):
            kept.append(it)
        if len(kept) == MAX_TOPICS:
            break
    kept.sort(key=lambda x: x[0])
    return [TopicDraft(i, s, e, summ, q) for i, (s, e, summ, q) in enumerate(kept, start=1)]


# ---------- Claude②：仕上げ ----------

def muted_in(a: float, b: float, muted: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(max(a, s), min(b, e)) for s, e in muted if s < b and e > a]


def draft_text(d: TopicDraft, start_lines: list[Line], end_lines: list[Line], peaks: list[Candidate],
               peak_t: dict[int, list[Line]] | None = None, muted: list[tuple[float, float]] = ()) -> str:
    inside = [p for p in peaks if d.start_sec <= p.peak_sec < d.end_sec]
    peak_lines = [f"・{hms(p.peak_sec)}（{p.peak_sec:.0f}秒）スコア{p.score}：" +
                  "　".join(f"{t}×{n}" for t, n in p.top_messages[:4]) for p in inside[:12]]
    # 区間の真ん中で何が起きたかを、盛り上がりの大きい山場の文字起こしで伝える
    talk = []
    for p in sorted([p for p in inside if (peak_t or {}).get(p.id)], key=lambda p: -p.score)[:PEAK_TRANSCRIPTS]:
        talk += [f"・{hms(p.peak_sec)}の山場の前後：", format_lines(peak_t[p.id])]
    lo, hi = d.start_sec - ADJUST_SEC, d.end_sec + ADJUST_SEC
    mute = "、".join(f"{hms(s)}〜{hms(e)}（{s:.0f}〜{e:.0f}秒）" for s, e in muted_in(lo, hi, list(muted)))
    return "\n".join([
        f"### 案{d.id}（{hms(d.start_sec)}〜{hms(d.end_sec)}、{d.start_sec:.0f}〜{d.end_sec:.0f}秒、面白さ{d.quality}）",
        f"要約：{d.summary}",
        *([f"音が消されている区間（含めない）：{mute}"] if mute else []),
        "始まりの前後の文字起こし：", format_lines(start_lines),
        "終わりの前後の文字起こし：", format_lines(end_lines),
        "区間内のチャットの山場：", "\n".join(peak_lines) or "なし",
        "区間内の山場の文字起こし：", "\n".join(talk) or "（文字起こしなし）",
        "代表的な画面：",
    ])


def final_content(template: str, video: VideoInfo, drafts: list[TopicDraft], start_t: dict, end_t: dict,
                  peaks: list[Candidate], frames: dict[int, Path], peak_t: dict[int, list[Line]] | None = None,
                  muted: list[tuple[float, float]] = ()) -> list[dict]:
    head, _, tail = template.partition("{topics}")
    fill = lambda s: (s.replace("{stream_title}", video.title or "不明")
                      .replace("{date}", jst_date(video.created_at).replace("-", "/"))
                      .replace("{vod_url}", vod_url(video.vod_id)))
    blocks: list[dict] = [{"type": "text", "text": fill(head)}]
    for d in drafts:
        blocks.append({"type": "text", "text": draft_text(d, start_t.get(d.id, []), end_t.get(d.id, []), peaks,
                                                          peak_t, muted)})
        f = frames.get(d.id)
        if f and Path(f).exists():
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.standard_b64encode(Path(f).read_bytes()).decode()}})
        else:
            blocks.append({"type": "text", "text": "（画像なし）"})
    blocks.append({"type": "text", "text": fill(tail)})
    return blocks


def date_tag(video: VideoInfo) -> str:
    return f"【{jst_date(video.created_at).replace('-', '/')}】"


def prefers(v: "TopicVideo", words) -> bool:
    """その題材か。Twitchのカテゴリが変わらないまま大会を見ることがあるので、タイトルも見る。
    words はカンマ区切りの文字列（例 "apex,algs"）か、言葉の並び。"""
    if isinstance(words, str):
        words = words.split(",")
    text = f"{getattr(v, 'game', '') or ''} {v.title}".lower()
    return any(w.strip() and w.strip().lower() in text for w in words)


# タイトルの先頭の【】がこれなら、Twitchのカテゴリより優先してゲーム名にする
# （カテゴリがARCのままALGSを観戦した回で、概要欄・タグ・再生リストがARCになったため。2026-11-02）
TITLE_GAMES = {"ALGS": "Apex Legends", "Apex": "Apex Legends", "BLEACH": "BLEACH Rebirth of Souls",
               "シャドバ": "Shadowverse: Worlds Beyond", "ナルト": "NARUTO X BORUTO Ultimate Ninja STORM CONNECTIONS"}


def game_from_title(v: "TopicVideo") -> "TopicVideo":
    m = re.match(r"【([^】]+)】", v.title)
    g = TITLE_GAMES.get(m.group(1)) if m else None
    if not g or g.split()[0].lower() in (v.game or "").lower():
        return v
    return replace(v, game=g, description=v.description.removeprefix(f"{v.game}。") if v.game else v.description)


AVOID_OK_QUALITY = 9   # 後回しの題材でも、これ以上面白ければふつうの扱い


def rank(v: "TopicVideo", prefer, avoid) -> tuple:
    """単体動画を選ぶ順（大きいほど先）：優先の題材 → ふつう → 後回しの題材。同じ段は面白さの順。"""
    if prefers(v, prefer):
        tier = 2
    elif prefers(v, avoid) and v.quality < AVOID_OK_QUALITY:
        tier = 0
    else:
        tier = 1
    return (tier, v.quality)


# 文字起こしやClaudeが繰り返し間違える固有名詞（見つけたら足す）
WORD_FIXES = {"愛染": "藍染"}


def fix_words(text: str) -> str:
    for wrong, right in WORD_FIXES.items():
        text = text.replace(wrong, right)
    return text


def fix_title(title: str, video: VideoInfo, fallback: str) -> str:
    """山かっこを全角にし、最後を【配信日】でそろえ、100字に収める。"""
    tag = date_tag(video)
    title = fix_words(title.strip().replace("<", "＜").replace(">", "＞")) or fallback
    if not title.endswith("】"):
        title = title[: 100 - len(tag)] + tag
    return title[:100]


def normalize_video(raw: dict, d: TopicDraft, video: VideoInfo) -> TopicVideo:
    lo, hi = max(0.0, d.start_sec - ADJUST_SEC), min(video.length_sec, d.end_sec + ADJUST_SEC)
    a = clamp(float(raw.get("in_sec", d.start_sec)), lo, hi)
    b = clamp(float(raw.get("out_sec", d.end_sec)), lo, hi)
    if b - a < 60:
        a, b = d.start_sec, d.end_sec
    title = fix_title(str(raw.get("title", "")), video, d.summary)
    chapters: list[tuple[float, str]] = []
    for c in sorted(raw.get("chapters", []), key=lambda c: float(c.get("sec", 0))):
        s = clamp(float(c.get("sec", a)), a, b - 1)
        label = truncate_display(str(c.get("label", "")).strip(), LABEL_MAX)
        if not label or (chapters and s - chapters[-1][0] < MIN_CHAPTER_GAP):
            continue
        chapters.append((s, label))
    if not chapters or chapters[0][0] > a:
        chapters.insert(0, (a, "冒頭"))
    else:
        chapters[0] = (a, chapters[0][1])
    f = raw.get("face") or {}
    face = None
    if float(f.get("fsize", 0)) > 0:
        face = clamp_square(float(f.get("fx", 0.88)), float(f.get("fy", 0.75)), float(f.get("fsize", 0.35)), lo=0.15)
    ts = float(raw.get("thumb_sec", -1))
    if not a <= ts < b:
        ts = (a + b) / 2
    return TopicVideo(
        topic_id=d.id, in_sec=round(a, 2), out_sec=round(b, 2), title=title,
        thumb_text=truncate_display(str(raw.get("thumb_text", "")).strip(), 16), thumb_sec=round(ts, 2),
        face=face, chapters=tuple(chapters), description=str(raw.get("description", "")).strip(),
        bubbles=tuple(truncate_display(str(x).strip(), 9) for x in (raw.get("bubbles") or [])[:2] if str(x).strip()),
        quality=int(raw.get("quality", d.quality)), game=video.game_at(a),
        thumb_emph=str(raw.get("thumb_emph", "")).strip(),
    )


def normalize_final(raw: dict, drafts: list[TopicDraft], video: VideoInfo) -> list[TopicVideo]:
    by_id = {d.id: d for d in drafts}
    out: dict[int, TopicVideo] = {}
    for v in raw.get("videos", []):
        tid = v.get("topic_id")
        if tid in by_id and tid not in out:
            out[tid] = normalize_video(v, by_id[tid], video)
    # Claude② が落とした案も、区切りどおりの最低限の形で残す
    for d in drafts:
        out.setdefault(d.id, normalize_video({}, d, video))
    return sorted(out.values(), key=lambda v: v.in_sec)


# ---------- 無音区間（Twitch が著作権の音楽などで消した部分）を避ける ----------

def clean_span(a: float, b: float, muted: list[tuple[float, float]]) -> tuple[float, float] | None:
    """[a, b) から無音区間を除いた、いちばん長い部分。MIN_TOPIC_SEC 未満しか残らなければ None。"""
    pieces = [(a, b)]
    for s, e in muted:
        nxt = []
        for x, y in pieces:
            if e <= x or s >= y:
                nxt.append((x, y))
                continue
            if s > x:
                nxt.append((x, s))
            if e < y:
                nxt.append((e, y))
        pieces = nxt
    if not pieces:
        return None
    x, y = max(pieces, key=lambda p: p[1] - p[0])
    return (x, y) if y - x >= MIN_TOPIC_SEC else None


def avoid_muted_drafts(drafts: list[TopicDraft], muted: list[tuple[float, float]]) -> list[TopicDraft]:
    out = []
    for d in drafts:
        sp = clean_span(d.start_sec, d.end_sec, muted)
        if sp:
            out.append(replace(d, start_sec=sp[0], end_sec=sp[1]))
    return out


def avoid_muted_video(v: TopicVideo, muted: list[tuple[float, float]]) -> TopicVideo | None:
    """Claude② が開始・終了を動かして無音区間に入ったときの後始末。短くなりすぎたら None（作らない）。"""
    sp = clean_span(v.in_sec, v.out_sec, muted)
    if sp is None:
        return None
    a, b = sp
    if (a, b) == (v.in_sec, v.out_sec):
        return v
    chapters = [(s, label) for s, label in v.chapters if a <= s < b]
    if not chapters or chapters[0][0] > a:
        chapters.insert(0, (a, "冒頭"))
    ts = v.thumb_sec if a <= v.thumb_sec < b else (a + b) / 2
    return replace(v, in_sec=round(a, 2), out_sec=round(b, 2), chapters=tuple(chapters), thumb_sec=round(ts, 2))


def max_length(v: TopicVideo, o) -> float:
    """その話題の長さの上限。prefer は topic_max_sec（60分）、avoid（ARC）とふつうの題材は20分（2026-11-04 オーナー判断）。"""
    if prefers(v, o.prefer):
        return o.topic_max_sec
    return o.avoid_max_sec if prefers(v, o.avoid) else o.normal_max_sec


def cap_length(v: TopicVideo, peak_scores: list[tuple[float, float]], max_sec: float) -> TopicVideo:
    """長すぎる話題を、山場（元配信の秒, スコア）がいちばん多く入る max_sec の区間に切り詰める。"""
    if max_sec <= 0 or v.out_sec - v.in_sec <= max_sec:
        return v
    inside = [(t, s) for t, s in peak_scores if v.in_sec <= t < v.out_sec]
    starts = sorted({v.in_sec, *(max(v.in_sec, min(t - 60, v.out_sec - max_sec)) for t, _ in inside)})
    a = max(starts, key=lambda x: (sum(s for t, s in inside if x <= t < x + max_sec), -x))
    b = a + max_sec
    chapters = [(t, label) for t, label in v.chapters if a <= t < b]
    if not chapters or chapters[0][0] > a:
        chapters.insert(0, (a, "冒頭"))
    ts = v.thumb_sec if a <= v.thumb_sec < b else (a + b) / 2
    return replace(v, in_sec=round(a, 2), out_sec=round(b, 2), chapters=tuple(chapters), thumb_sec=round(ts, 2))


# ---------- タイトルの点検（Claude③） ----------

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {
        "type": "object",
        "properties": {"topic_id": {"type": "integer"}, "ok": {"type": "boolean"},
                       "reason": {"type": "string"}, "title": {"type": "string"}},
        "required": ["topic_id", "ok", "reason", "title"], "additionalProperties": False}}},
    "required": ["titles"], "additionalProperties": False,
}


def check_content(template: str, video: VideoInfo, vids: list[TopicVideo], drafts: list[TopicDraft],
                  start_t: dict, end_t: dict, peaks: list[Candidate], peak_t: dict[int, list[Line]] | None = None
                  ) -> list[dict]:
    by_id = {d.id: d for d in drafts}
    parts = []
    for v in vids:
        d = by_id.get(v.topic_id)
        if d is None:
            continue
        mats = draft_text(replace(d, start_sec=v.in_sec, end_sec=v.out_sec), start_t.get(d.id, []),
                          end_t.get(d.id, []), peaks, peak_t).removesuffix("代表的な画面：").rstrip()
        parts.append("\n".join([mats, f"今のタイトル：{v.title}", f"概要欄：{v.description}"]))
    text = (template.replace("{stream_title}", video.title or "不明")
            .replace("{date}", jst_date(video.created_at).replace("-", "/"))
            .replace("{videos}", "\n\n".join(parts)))
    return [{"type": "text", "text": text}]


def apply_checked(raw: dict, vids: list[TopicVideo], video: VideoInfo) -> tuple[list[TopicVideo], list[str]]:
    """点検で直したタイトルに差し替える。直した内容を (動画, ログ用の説明) で返す。"""
    fixed = {t.get("topic_id"): t for t in raw.get("titles", []) if not t.get("ok") and str(t.get("title", "")).strip()}
    out, notes = [], []
    for v in vids:
        t = fixed.get(v.topic_id)
        if t:
            new = fix_title(str(t["title"]), video, v.title)
            if new != v.title:
                notes.append(f"「{v.title}」→「{new}」（{str(t.get('reason', '')).strip()}）")
                v = replace(v, title=new)
        out.append(v)
    return out, notes


def fallback_drafts(cands: list[Candidate], length_sec: float, span: float = 900.0) -> list[TopicDraft]:
    """Claude①が使えないとき：盛り上がりの大きい山場の前後 span 秒を区間にする（重なりは除く）。"""
    raw = {"topics": [{"start_sec": max(0, c.peak_sec - span / 2), "end_sec": min(length_sec, c.peak_sec + span / 2),
                       "summary": f"{hms(c.peak_sec)}の山場", "quality": int(min(10, c.score))}
                      for c in sorted(cands, key=lambda c: -c.score)]}
    return normalize_plan(raw, length_sec)

