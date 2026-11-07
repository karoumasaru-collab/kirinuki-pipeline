"""candidates.md（iPhoneで読んで採否を判断する一覧）の生成。純粋関数。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kirinuki.chat import VideoInfo
from kirinuki.comments import normalize_for_ng
from kirinuki.fetch import vod_url
from kirinuki.peaks import Candidate

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class ShortResult:
    cand_id: int
    filename: str
    start_sec: float
    end_sec: float
    comments_shown: int
    comments_total: int
    title: str = ""
    thumb: str = ""
    quality: int = 0


def hms(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def twitch_time_link(vod_id: str, sec: float) -> str:
    s = int(sec)
    return f"{vod_url(vod_id)}?t={s // 3600}h{s % 3600 // 60}m{s % 60}s"


def jst_date(iso_utc: str, fallback: datetime | None = None) -> str:
    """配信日（日本時間）を YYYY-MM-DD で返す。"""
    if iso_utc:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone(JST).strftime("%Y-%m-%d")
    return (fallback or datetime.now(JST)).astimezone(JST).strftime("%Y-%m-%d")


def build_candidates_md(
    video: VideoInfo,
    candidates: list[Candidate],
    shorts: list[ShortResult],
    ng_words: tuple[str, ...],
    generated_at: datetime,
    selections: dict | None = None,
    method: str = "チャットの盛り上がりスコア順（Claude未使用）",
) -> str:
    """selections は {cand_id: Selection}（select.py）。無ければ採否欄を出さない。"""
    by_id = {s.cand_id: s for s in shorts}
    selections = selections or {}
    lines = [
        f"# 候補一覧：{video.title or video.vod_id}",
        "",
        f"- 元配信：{vod_url(video.vod_id)}",
        f"- ゲーム・企画：{video.game or '不明'}",
        f"- 配信日：{jst_date(video.created_at, generated_at)}　長さ：{hms(video.length_sec)}",
        f"- 作成：{generated_at.astimezone(JST).strftime('%Y-%m-%d %H:%M')}（日本時間）",
        f"- 選び方：{method}",
        "",
        "## ショートにしたもの",
        "",
    ]
    if shorts:
        for s in shorts:
            lines.append(f"- **{s.title or s.filename}**")
            lines.append(
                f"  - {s.filename}　{hms(s.start_sec)}〜{hms(s.end_sec)}（{s.end_sec - s.start_sec:.0f}秒）"
                f"　候補{s.cand_id}　コメント {s.comments_shown}件を表示／区間内 {s.comments_total}件"
                + (f"　サムネ {s.thumb}" if s.thumb else "")
            )
    else:
        lines.append("- なし")
    lines += ["", "## 候補（スコアの高い順）", ""]
    for rank, c in enumerate(sorted(candidates, key=lambda c: c.score, reverse=True), start=1):
        s = by_id.get(c.id)
        sel = selections.get(c.id)
        mark = f"　✅ {s.filename}" if s else ""
        lines.append(f"### {rank}. {hms(c.start_sec)}〜{hms(c.end_sec)}　スコア {c.score}{mark}")
        lines.append("")
        lines.append(f"[元配信で見る]({twitch_time_link(video.vod_id, c.start_sec)})（候補{c.id}）")
        if sel is not None:
            verdict = "採用" if sel.keep else "不採用"
            lines.append("")
            lines.append(f"- {verdict}（{sel.kind}・面白さ {sel.quality}）：{sel.reason}")
            if sel.keep:
                lines.append(f"- タイトル：{sel.title}")
                lines.append(f"- 黒帯：{sel.band[0]}／{sel.band[1]}　サムネ：{sel.thumb_text}"
                             f"　拡大：{sel.crop.mode}")
                lines.append(f"- 区間：{hms(sel.in_sec)}〜{hms(sel.out_sec)}（{sel.out_sec - sel.in_sec:.0f}秒）")
                if sel.hook:
                    lines.append(f"- 見どころ：{sel.hook}")
        msgs = [
            f"{text}×{n}" for text, n in c.top_messages
            if not any(w in normalize_for_ng(text) for w in ng_words)
        ][:6]
        if msgs:
            lines.append("")
            lines.append("多かったチャット：" + "　".join(msgs))
        lines.append("")
    return "\n".join(lines)
