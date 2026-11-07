"""候補区間だけを yt-dlp でダウンロードする（DESIGN.md 3.4）。配信全体は落とさない。"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Claudeが開始・終了を微調整できるよう、候補の前後に付ける余白
PAD_SEC = 5.0


@dataclass(frozen=True)
class Segment:
    path: Path
    start_sec: float  # 元配信での区間の開始秒（要求した値。実際はキーフレーム位置で最大数秒ずれうる）
    end_sec: float


def vod_url(vod_id: str) -> str:
    return f"https://www.twitch.tv/videos/{vod_id}"


def padded_range(start_sec: float, end_sec: float, length_sec: float | None = None) -> tuple[float, float]:
    start = max(0.0, start_sec - PAD_SEC)
    end = end_sec + PAD_SEC
    if length_sec:
        end = min(end, length_sec)
    return start, end


def _download(vod_id: str, fmt: str, start: float, end: float, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{name}.*"):
        old.unlink()
    subprocess.run(
        [sys.executable, "-m", "yt_dlp", vod_url(vod_id),
         "-f", fmt,
         "--download-sections", f"*{start:.2f}-{end:.2f}",
         "--no-progress", "--no-playlist", "--quiet", "--no-warnings",
         "-o", str(out_dir / f"{name}.%(ext)s")],
        check=True,
    )
    files = [p for p in out_dir.glob(f"{name}.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise FileNotFoundError(f"区間ファイルが見つからない: {out_dir}/{name}.*")
    return files[0]


def fetch_segment(vod_id: str, start_sec: float, end_sec: float, out_dir: Path, name: str,
                  length_sec: float | None = None) -> Segment:
    """1080pの映像（余白付き）。"""
    start, end = padded_range(start_sec, end_sec, length_sec)
    path = _download(vod_id, "best[height<=1080]", start, end, out_dir, name)
    return Segment(path=path, start_sec=start, end_sec=end)


def fetch_audio(vod_id: str, start_sec: float, end_sec: float, out_dir: Path, name: str,
                length_sec: float | None = None) -> Segment:
    """文字起こし用の音声だけ（余白付き）。"""
    start, end = padded_range(start_sec, end_sec, length_sec)
    path = _download(vod_id, "audio_only/bestaudio/worst", start, end, out_dir, name)
    return Segment(path=path, start_sec=start, end_sec=end)


def fetch_frame(vod_id: str, at_sec: float, out_dir: Path, name: str) -> Path:
    """at_sec の瞬間の静止画（480p程度のJPG）。Claudeに拡大位置を決めてもらうため。"""
    start = max(0.0, at_sec - 1.0)
    clip = _download(vod_id, "best[height<=480]/worst", start, at_sec + 1.0, out_dir, f"{name}_clip")
    jpg = out_dir / f"{name}.jpg"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.8", "-i", str(clip),
         "-frames:v", "1", "-vf", "scale=854:-2", "-q:v", "4", str(jpg)],
        check=True,
    )
    clip.unlink()
    return jpg
