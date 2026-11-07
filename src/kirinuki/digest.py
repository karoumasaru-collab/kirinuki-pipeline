"""最新速報（横1920x1080、8〜15分、チャプター付き）の組み立て（DESIGN.md 3.7）。

チャプター計算・概要欄の組み立ては純粋関数。書き出しは ffmpeg。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kirinuki import overlay
from kirinuki.chat import VideoInfo
from kirinuki.fetch import Segment
from kirinuki.render import ENCODE_DIGEST, LOUDNORM
from kirinuki.report import jst_date, twitch_time_link

FADE_SEC = 0.3
MIN_CHAPTER_SEC = 10.0   # YouTubeのチャプターは各10秒以上
DIGEST_W, DIGEST_H = 1920, 1080
THUMB_W, THUMB_H = 1280, 720


# ---------- 純粋関数 ----------

def timestamp(sec: float) -> str:
    s = int(sec)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def chapters(parts: list[tuple[str, float]]) -> list[tuple[float, str]]:
    """[(ラベル, 長さ秒)] → [(開始秒, ラベル)]。最初は必ず0。10秒未満の区間は前のチャプターにまとめる。"""
    out: list[tuple[float, str]] = []
    t = 0.0
    for label, dur in parts:
        if not out or dur >= MIN_CHAPTER_SEC:
            out.append((t, label))
        t += dur
    # 最後のチャプターが10秒未満になったら、前とまとめる
    if len(out) >= 2 and t - out[-1][0] < MIN_CHAPTER_SEC:
        out.pop()
    return out


def description(video: VideoInfo, digest, tags: list[str], channel_line: str) -> str:
    """速報の概要欄。Claudeの説明＋チャプター＋元配信（場面ごとの時刻付きリンク）＋ハッシュタグ。"""
    parts = [(i.label, i.sel.out_sec - i.sel.in_sec) for i in digest.items]
    lines = [digest.description, "", "▼チャプター"]
    lines += [f"{timestamp(t)} {label}" for t, label in chapters(parts)]
    lines += ["", f"▼元配信（{jst_date(video.created_at).replace('-', '/')}「{video.title}」）",
              f"https://www.twitch.tv/videos/{video.vod_id}"]
    lines += [f"・{i.label}：{twitch_time_link(video.vod_id, i.sel.in_sec)}" for i in digest.items]
    lines += ["", channel_line, " ".join(dict.fromkeys(tags))]
    return "\n".join(lines).replace("<", "＜").replace(">", "＞").strip()


# ---------- 書き出し ----------

def render_part(seg: Segment, start: float, end: float, out_path: Path, ass_path: Path | None) -> None:
    """1場面を横1920x1080・30fpsで書き出す。音は前後0.3秒でフェード。"""
    dur = end - start
    v = (f"[0:v]scale={DIGEST_W}:{DIGEST_H}:force_original_aspect_ratio=decrease,"
         f"pad={DIGEST_W}:{DIGEST_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")
    if ass_path:
        v += f",ass=filename={ass_path.name}"
    a = (f"[0:a]{LOUDNORM},afade=t=in:st=0:d={FADE_SEC},"
         f"afade=t=out:st={max(0.0, dur - FADE_SEC):.3f}:d={FADE_SEC}[a]")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{max(0.0, start - seg.start_sec):.3f}", "-i", str(seg.path.resolve()),
        "-t", f"{dur:.3f}", "-filter_complex", f"{v}[v];{a}", "-map", "[v]", "-map", "[a]",
        *ENCODE_DIGEST, str(out_path.resolve()),
    ], check=True, cwd=(ass_path or seg.path).parent)


def concat(parts: list[Path], out_path: Path) -> None:
    lst = out_path.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out_path)], check=True)
    lst.unlink()


# 速報サムネのレイアウト（1280x720）：左上にゲーム名、右に加藤さんの顔（白枠）、
# 左にチャットの吹き出し、下に赤い帯と大きな白文字
FACE_SIZE = 420
FACE_X, FACE_Y = THUMB_W - FACE_SIZE - 28, 118
BAND_Y = 556
RED_HEX = "0xE62E2E"


def thumb_ass(text: str, game: str = "", bubbles: tuple[str, ...] = ()) -> str:
    styles = [
        overlay.style("Cap", overlay.TITLE_FONT, 150, overlay.WHITE, 9, alignment=5),
        overlay.style("Game", overlay.TITLE_FONT, 104, overlay.WHITE, 10, alignment=7),
        # BorderStyle=3（不透明の箱）で白い吹き出しに黒文字
        "Style: Bubble," + overlay.TITLE_FONT + ",54,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,"
        "-1,0,0,0,100,100,0,0,3,10,0,7,0,0,0,1",
    ]
    events = []
    if text:
        size = overlay.fit_size(text, 150, width=THUMB_W - 60)
        events.append(overlay.static_event(0, 10, "Cap", THUMB_W / 2, BAND_Y + (THUMB_H - BAND_Y) / 2, text, size))
    if game:
        events.append(f"Dialogue: 1,0:00:00.00,0:00:10.00,Game,,0,0,0,,{{\\an7\\pos(36,26)}}{overlay.ass_escape(game)}")
    for k, b in enumerate(bubbles[:2]):
        y = 300 + k * 110
        events.append(f"Dialogue: 1,0:00:00.00,0:00:10.00,Bubble,,0,0,0,,{{\\an7\\pos(44,{y})}}{overlay.ass_escape(b)}")
    return overlay.ass_document(THUMB_W, THUMB_H, styles, events)


def thumb_filter(face: tuple[float, float, float] | None, ass_name: str) -> str:
    """背景＝その瞬間の画面、右に顔の拡大（白枠）、下に赤い帯、文字はASS。"""
    bg = (f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,crop={THUMB_W}:{THUMB_H},"
          f"eq=saturation=1.25:contrast=1.08")
    band = f"drawbox=x=0:y={BAND_Y}:w={THUMB_W}:h={THUMB_H - BAND_Y}:color={RED_HEX}:t=fill"
    if face is None:
        return f"[0:v]{bg},{band},ass=filename={ass_name}[v]"
    fx, fy, fs = face
    s = f"ih*{fs:.4f}"
    return (f"[0:v]split=2[a][b];[a]{bg},{band}[bg];"
            f"[b]crop=w={s}:h={s}:x=iw*{fx:.4f}-{s}/2:y=ih*{fy:.4f}-{s}/2,"
            f"scale={FACE_SIZE}:{FACE_SIZE},drawbox=x=0:y=0:w=iw:h=ih:color=white:t=10[face];"
            f"[bg][face]overlay={FACE_X}:{FACE_Y},ass=filename={ass_name}[v]")


def render_thumbnail(seg: Segment, at_sec: float, text: str, out_path: Path, game: str = "",
                     face: tuple[float, float, float] | None = None, bubbles: tuple[str, ...] = ()) -> None:
    """速報サムネ（1280x720）。"""
    ass = out_path.with_suffix(".ass")
    ass.write_text(thumb_ass(text, game, bubbles), encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{max(0.0, at_sec - seg.start_sec):.3f}", "-i", str(seg.path.resolve()),
        "-filter_complex", thumb_filter(face, ass.name), "-map", "[v]",
        "-frames:v", "1", "-q:v", "3", str(out_path.resolve()),
    ], check=True, cwd=ass.parent)
    ass.unlink()
