"""Claude による選定・命名・拡大位置の決定（DESIGN.md 3.6）。

API呼び出し以外（入力の組み立て、出力の検証・補正、Claudeが使えないときの代替）は純粋関数。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from kirinuki.chat import VideoInfo
from kirinuki.comments import normalize_for_ng
from kirinuki.fetch import vod_url
from kirinuki.overlay import truncate_display
from kirinuki.peaks import Candidate
from kirinuki.report import hms
from kirinuki.transcribe import Line, format_lines

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "select_clips.md"
SRC_ASPECT = 16 / 9
TITLE_MAX, BAND_MAX, THUMB_MAX = 40, 10, 10
DIGEST_THUMB_MAX = 13       # 速報サムネの大きな文字（指示は8〜12字）
MIN_CLIP_SEC = 5.0
SCENE_MAX_SEC = 150.0     # 速報の1場面の上限（Claudeへの指示は120秒程度）
LABEL_MAX = 15

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cand_id": {"type": "integer"},
                    "keep": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "kind": {"type": "string", "enum": ["short", "digest", "both", "none"]},
                    "in_sec": {"type": "number"},
                    "out_sec": {"type": "number"},
                    "short_in_sec": {"type": "number"},
                    "short_out_sec": {"type": "number"},
                    "title": {"type": "string"},
                    "band": {"type": "array", "items": {"type": "string"}},
                    "thumb_text": {"type": "string"},
                    "thumb_sec": {"type": "number"},
                    "crop": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["both", "game", "face"]},
                            "cx": {"type": "number"},
                            "cy": {"type": "number"},
                            "size": {"type": "number"},
                            "fx": {"type": "number"},
                            "fy": {"type": "number"},
                            "fsize": {"type": "number"},
                        },
                        "required": ["mode", "cx", "cy", "size", "fx", "fy", "fsize"],
                        "additionalProperties": False,
                    },
                    "hook": {"type": "string"},
                    "quality": {"type": "integer"},
                },
                "required": ["cand_id", "keep", "reason", "kind", "in_sec", "out_sec",
                             "short_in_sec", "short_out_sec", "title",
                             "band", "thumb_text", "thumb_sec", "crop", "hook", "quality"],
                "additionalProperties": False,
            },
        },
        "digests": {
            "type": "array",
            "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "order": {"type": "array", "items": {"type": "integer"}},
                "chapters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"cand_id": {"type": "integer"}, "label": {"type": "string"}},
                        "required": ["cand_id", "label"],
                        "additionalProperties": False,
                    },
                },
                "description": {"type": "string"},
                "thumb_text": {"type": "string"},
            },
            "required": ["title", "order", "chapters", "description", "thumb_text"],
            "additionalProperties": False,
            },
        },
    },
    "required": ["clips", "digests"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Crop:
    """大きく映す正方形（cx, cy, size）と、both のときの小窓のカメラ部分（fx, fy, fsize）。"""
    mode: str = "game"
    cx: float = 0.5
    cy: float = 0.5
    size: float = 1.0
    fx: float = 0.0
    fy: float = 0.0
    fsize: float = 0.0

    def main(self) -> tuple[float, float, float]:
        return (self.cx, self.cy, self.size)

    def pip(self) -> tuple[float, float, float] | None:
        return (self.fx, self.fy, self.fsize) if self.mode == "both" else None


@dataclass(frozen=True)
class Selection:
    cand_id: int
    keep: bool
    reason: str
    kind: str
    in_sec: float
    out_sec: float
    title: str
    band: tuple[str, str]
    thumb_text: str
    crop: Crop = field(default_factory=Crop)
    hook: str = ""
    quality: int = 0
    by_claude: bool = True
    thumb_sec: float = -1.0  # 区間外（-1など）ならパイプライン側で山場の時刻を使う
    short_in_sec: float = -1.0   # ショート用の区間（場面 in/out の中、60秒以内）。-1 なら in/out を使う
    short_out_sec: float = -1.0

    def short_range(self) -> tuple[float, float]:
        if self.short_in_sec >= 0 and self.short_out_sec > self.short_in_sec:
            return self.short_in_sec, self.short_out_sec
        return self.in_sec, self.out_sec


@dataclass(frozen=True)
class DigestItem:
    sel: Selection
    label: str


@dataclass(frozen=True)
class Digest:
    title: str
    items: tuple[DigestItem, ...]
    description: str
    thumb_text: str

    @property
    def total_sec(self) -> float:
        return sum(i.sel.out_sec - i.sel.in_sec for i in self.items)


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int


# ---------- 純粋関数 ----------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def clamp_square(cx: float, cy: float, size: float, lo: float = 0.3) -> tuple[float, float, float]:
    """正方形の切り抜きが16:9の映像からはみ出さないように補正する。"""
    size = clamp(size, lo, 1.0)
    half_w = size / SRC_ASPECT / 2
    return clamp(cx, half_w, 1 - half_w), clamp(cy, size / 2, 1 - size / 2), size


def clamp_crop(c: Crop) -> Crop:
    mode = c.mode if c.mode in ("both", "game", "face") else "game"
    cx, cy, size = clamp_square(c.cx, c.cy, c.size)
    if mode != "both":
        return Crop(mode, cx, cy, size)
    if c.fsize <= 0:
        # 小窓の指定が無ければ、加藤さんのカメラがよくある右下を使う
        fx, fy, fsize = clamp_square(0.88, 0.75, 0.35, lo=0.15)
    else:
        fx, fy, fsize = clamp_square(c.fx, c.fy, c.fsize, lo=0.15)
    return Crop(mode, cx, cy, size, fx, fy, fsize)


def normalize_clip(raw: dict, cand: Candidate, fetched: tuple[float, float], short_max: float) -> Selection:
    lo, hi = fetched
    in_sec = clamp(float(raw.get("in_sec", cand.start_sec)), lo, hi)
    out_sec = clamp(float(raw.get("out_sec", cand.end_sec)), lo, hi)
    if out_sec - in_sec < MIN_CLIP_SEC:
        in_sec, out_sec = cand.start_sec, cand.end_sec
    if out_sec - in_sec > SCENE_MAX_SEC:
        in_sec = out_sec - SCENE_MAX_SEC  # オチ（後ろ側）を残す
    kind = raw.get("kind", "none")
    keep = bool(raw.get("keep")) and kind != "none"
    # ショートの区間：場面の中に収め、60秒以内。おかしければ場面の終わりから60秒
    s_in = clamp(float(raw.get("short_in_sec", in_sec)), in_sec, out_sec)
    s_out = clamp(float(raw.get("short_out_sec", out_sec)), in_sec, out_sec)
    if s_out - s_in < MIN_CLIP_SEC:
        s_in, s_out = max(in_sec, out_sec - short_max), out_sec
    if s_out - s_in > short_max:
        s_out = s_in + short_max
    band = list(raw.get("band") or []) + ["", ""]
    c = raw.get("crop") or {}
    return Selection(
        cand_id=cand.id,
        keep=keep,
        reason=str(raw.get("reason", "")).strip(),
        kind=kind if keep else "none",
        in_sec=round(in_sec, 2),
        out_sec=round(out_sec, 2),
        title=str(raw.get("title", "")).strip()[:TITLE_MAX],
        band=(truncate_display(str(band[0]).strip(), BAND_MAX),
              truncate_display(str(band[1]).strip(), BAND_MAX)),
        thumb_text=truncate_display(str(raw.get("thumb_text", "")).strip(), THUMB_MAX),
        crop=clamp_crop(Crop(c.get("mode", "both"), float(c.get("cx", 0.5)), float(c.get("cy", 0.5)),
                             float(c.get("size", 1.0)), float(c.get("fx", 0)), float(c.get("fy", 0)),
                             float(c.get("fsize", 0)))),
        hook=str(raw.get("hook", "")).strip(),
        quality=int(raw.get("quality", 0)),
        thumb_sec=float(raw.get("thumb_sec", -1.0)),
        short_in_sec=round(s_in, 2),
        short_out_sec=round(s_out, 2),
    )


def normalize_all(raw: dict, cands: list[Candidate], fetched: dict[int, tuple[float, float]],
                  short_max: float) -> list[Selection]:
    by_id = {c.id: c for c in cands}
    out: dict[int, Selection] = {}
    for clip in raw.get("clips", []):
        cid = clip.get("cand_id")
        if cid in by_id and cid not in out:
            out[cid] = normalize_clip(clip, by_id[cid], fetched[cid], short_max)
    return [out[c.id] for c in cands if c.id in out]


def normalize_digest(raw: dict | None, sels: list[Selection], max_sec: float,
                     used: set[int] | None = None, short_ids: set[int] = frozenset(),
                     max_short_scenes: int = 99) -> Digest | None:
    """Claudeの速報構成を検証する。採用した場面だけ、order 順・重複なし・合計 max_sec まで。

    used（ほかの速報で使った場面）は入れない。ショートにした場面（short_ids）は max_short_scenes まで。
    """
    if not raw:
        return None
    used = used if used is not None else set()
    by_id = {s.cand_id: s for s in sels if s.keep and s.kind != "none"}
    labels = {c.get("cand_id"): str(c.get("label", "")).strip() for c in raw.get("chapters", [])}
    items, seen, total, n_short = [], set(), 0.0, 0
    for cid in raw.get("order", []):
        s = by_id.get(cid)
        if s is None or cid in seen or cid in used:
            continue
        if cid in short_ids and n_short >= max_short_scenes:
            continue
        dur = s.out_sec - s.in_sec
        if total + dur > max_sec:
            continue
        seen.add(cid)
        total += dur
        n_short += cid in short_ids
        items.append(DigestItem(s, truncate_display(labels.get(cid) or s.title, LABEL_MAX)))
    if len(items) < 2:
        return None
    return Digest(
        title=str(raw.get("title", "")).strip()[:90],
        items=tuple(items),
        description=str(raw.get("description", "")).strip(),
        thumb_text=truncate_display(str(raw.get("thumb_text", "")).strip(), DIGEST_THUMB_MAX),
    )


def normalize_digests(raw_list, sels: list[Selection], short_ids: set[int], max_sec: float,
                      max_count: int, max_short_scenes: int) -> list[Digest]:
    """複数の速報。場面が速報どうしで重ならないようにし、最大 max_count 本。"""
    if isinstance(raw_list, dict):  # 1本だけの古い形
        raw_list = [raw_list]
    used: set[int] = set()
    out = []
    for raw in (raw_list or [])[:max_count]:
        d = normalize_digest(raw, sels, max_sec, used, short_ids, max_short_scenes)
        if d:
            used |= {i.sel.cand_id for i in d.items}
            out.append(d)
    return out


def fallback_selection(cands: list[Candidate], video: VideoInfo, n: int) -> list[Selection]:
    """Claudeが使えないとき：スコア上位 n 件を、候補区間そのまま・中央拡大で採用する。"""
    top = {c.id for c in sorted(cands, key=lambda c: c.score, reverse=True)[:n]}
    return [
        Selection(
            cand_id=c.id, keep=c.id in top, reason="（Claude未使用：チャットのスコア順）",
            kind="short" if c.id in top else "none", in_sec=c.start_sec, out_sec=c.end_sec,
            title=f"{video.title} {hms(c.peak_sec)}"[:TITLE_MAX],
            band=((video.game or "")[:BAND_MAX], ""), thumb_text="", by_claude=False,
        )
        for c in cands
    ]


def choose_shorts(sels: list[Selection], cands: list[Candidate], n: int,
                  n_min: int = 0, min_quality: int = 0) -> list[Selection]:
    """ショートにするもの。面白さ min_quality 以上を最大 n 件、足りなければ n_min 件まで次点で補う。時刻順に並べる。"""
    score = {c.id: c.score for c in cands}
    ok = [s for s in sels if s.keep and s.kind in ("short", "both")]
    ok.sort(key=lambda s: (s.quality, score.get(s.cand_id, 0)), reverse=True)
    good = [s for s in ok if s.quality >= min_quality][:n]
    if len(good) < n_min:
        good = ok[: min(n, n_min)]
    return sorted(good, key=lambda s: s.short_range()[0])


def candidate_text(c: Candidate, fetched: tuple[float, float], lines: list[Line],
                   ng_words: tuple[str, ...], game: str = "") -> str:
    chats = [f"{t}×{n}" for t, n in c.top_messages if not any(w in normalize_for_ng(t) for w in ng_words)]
    lo, hi = fetched
    return "\n".join([
        f"### 候補{c.id}（盛り上がりスコア {c.score}）",
        f"取得済みの範囲：{lo:.1f}〜{hi:.1f}秒（{hms(lo)}〜{hms(hi)}）",
        f"チャットのピーク：{c.peak_sec:.1f}秒（{hms(c.peak_sec)}）" + (f"　ゲーム：{game}" if game else ""),
        f"多かったチャット：{'　'.join(chats) or 'なし'}",
        "文字起こし：",
        format_lines(lines),
        "山場の画面：",
    ])


def build_content(template: str, video: VideoInfo, cands: list[Candidate],
                  fetched: dict[int, tuple[float, float]], transcripts: dict[int, list[Line]],
                  frames: dict[int, Path], ng_words: tuple[str, ...]) -> list[dict]:
    head, _, tail = template.partition("{candidates}")
    fill = lambda s: (s.replace("{stream_title}", video.title or "不明")
                      .replace("{game_name}", video.game or "不明")
                      .replace("{vod_url}", vod_url(video.vod_id)))
    blocks: list[dict] = [{"type": "text", "text": fill(head)}]
    for c in cands:
        blocks.append({"type": "text", "text": candidate_text(c, fetched[c.id], transcripts.get(c.id, []), ng_words,
                                                              video.game_at(c.peak_sec))})
        frame = frames.get(c.id)
        if frame and frame.exists():
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(frame.read_bytes()).decode("ascii")}})
        else:
            blocks.append({"type": "text", "text": "（画像なし）"})
    blocks.append({"type": "text", "text": fill(tail)})
    return blocks


# ---------- API呼び出し ----------

def call_claude(model: str, content: list[dict], schema: dict | None = None) -> tuple[dict, Usage]:
    import anthropic

    client = anthropic.Anthropic()
    kwargs = dict(
        model=model,
        max_tokens=48000,  # 候補50件＋速報3本分のJSON。長いのでストリーミングで受ける
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema or SCHEMA}},
    )
    try:
        # 安全判定で断られたときは、Anthropic推奨の別モデルで自動的にやり直す
        with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs) as s:
            resp = s.get_final_message()
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        with client.messages.stream(**kwargs) as s:
            resp = s.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claudeが応答を断った：{getattr(resp, 'stop_details', None)}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("出力が上限で切れた（max_tokens）")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text), Usage(resp.model, resp.usage.input_tokens, resp.usage.output_tokens)
