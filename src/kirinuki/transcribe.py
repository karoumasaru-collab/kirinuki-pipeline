"""faster-whisper による文字起こし（DESIGN.md 3.5）。字幕には使わず、Claudeの判断材料にする。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from kirinuki.fetch import Segment


@dataclass(frozen=True)
class Line:
    start_sec: float  # 元配信の秒
    end_sec: float
    text: str


def load_audio(path, sr: int = 16000):
    """ffmpeg で16kHzモノラルのfloat配列にする。

    faster-whisper 内蔵の読み込み（PyAV）は版の組み合わせで壊れることがあるので使わない。
    """
    import numpy as np
    pcm = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(pcm, dtype=np.float32)


class Transcriber:
    def __init__(self, model: str = "small", compute_type: str = "int8"):
        from faster_whisper import WhisperModel  # 重いので使うときだけ読み込む
        self.model = WhisperModel(model, device="cpu", compute_type=compute_type)

    def transcribe(self, seg: Segment) -> list[Line]:
        segments, _ = self.model.transcribe(
            load_audio(seg.path), language="ja", vad_filter=True, beam_size=1)  # 判断材料なので速さ優先
        return [
            Line(seg.start_sec + s.start, seg.start_sec + s.end, s.text.strip())
            for s in segments if s.text.strip()
        ]


def format_lines(lines: list[Line]) -> str:
    from kirinuki.report import hms
    return "\n".join(f"[{hms(x.start_sec)}｜{x.start_sec:.1f}秒] {x.text}" for x in lines) or "（文字起こしなし）"


def transcribe_all(segs: dict[int, Segment], model: str, compute_type: str) -> dict[int, list[Line]]:
    t = Transcriber(model, compute_type)
    return {cid: t.transcribe(seg) for cid, seg in segs.items()}

