"""ffmpeg での書き出し（DESIGN.md 3.7）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kirinuki import overlay
from kirinuki.fetch import Segment

# loudnorm は内部で192kHzにするため、48kHzに戻さないと96kHzのAACになり、iPhone等で無音になる
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
# 速報は長いので容量を抑える（YouTube側で再圧縮されるので見た目の差は小さい）
# 長い動画（ダイジェスト・単体動画）は速さと容量を優先：superfast・crf 24・最大3Mbps
# （1080p30で30分＝約0.7GB。上限なしだと30分で約1.5GBになり、ドライブがすぐあふれた）
ENCODE_DIGEST = [{"20": "24", "veryfast": "superfast"}.get(x, x) for x in ENCODE][:6] + \
    ["-maxrate", "3000k", "-bufsize", "6000k"] + ENCODE[6:]


def _square_crop(cx: float, cy: float, size: float) -> str:
    s = f"ih*{size:.4f}"
    return f"crop=w={s}:h={s}:x=iw*{cx:.4f}-{s}/2:y=ih*{cy:.4f}-{s}/2"


def short_video_filter(cx: float, cy: float, size: float, ass_name: str | None,
                       pip: tuple[float, float, float] | None = None) -> str:
    """縦1080x1920：上は黒帯、中央は正方形に切り抜いた映像、下はぼかした背景。

    pip があれば、中央の映像の右下にカメラの小窓（白枠）を重ねる。文字はASSで焼き込む。
    """
    n = 3 if pip else 2
    # 背景のぼかしは1/4に縮めてからかけて戻す（見た目はほぼ同じで、処理は約1/10）。30fpsにそろえる
    v = (f"[0:v]fps=30,split={n}[a][b]{'[c]' if pip else ''};"
         f"[a]scale={overlay.SHORT_W // 4}:{overlay.SHORT_H // 4}:force_original_aspect_ratio=increase,"
         f"crop={overlay.SHORT_W // 4}:{overlay.SHORT_H // 4},boxblur=6:2,"
         f"scale={overlay.SHORT_W}:{overlay.SHORT_H},"
         f"drawbox=x=0:y=0:w={overlay.SHORT_W}:h={overlay.BAND_H}:color=black:t=fill[bg];"
         f"[b]{_square_crop(cx, cy, size)},scale={overlay.VIDEO_SIZE}:{overlay.VIDEO_SIZE}[fg];")
    if pip:
        v += (f"[c]{_square_crop(*pip)},scale={overlay.PIP_SIZE}:{overlay.PIP_SIZE},"
              f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=6[pip];"
              f"[bg][fg]overlay=0:{overlay.VIDEO_TOP}[base];"
              f"[base][pip]overlay={overlay.PIP_X}:{overlay.PIP_Y}")
    else:
        v += f"[bg][fg]overlay=0:{overlay.VIDEO_TOP}"
    if ass_name:
        v += f",ass=filename={ass_name}"
    return v + "[v]"


def render_short(seg: Segment, clip_start: float, clip_end: float, out_path: Path,
                 ass_path: Path | None = None, crop: tuple[float, float, float] = (0.5, 0.5, 1.0),
                 pip: tuple[float, float, float] | None = None) -> None:
    """seg の中から [clip_start, clip_end)（元配信の秒）を切り出して縦動画にする。

    ASSのファイル名をフィルタに渡すため、ffmpeg は ASS と同じフォルダで動かす。
    """
    workdir = (ass_path or seg.path).parent
    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph = short_video_filter(*crop, ass_path.name if ass_path else None, pip) + f";[0:a]{LOUDNORM}[a]"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{max(0.0, clip_start - seg.start_sec):.3f}",
        "-i", str(seg.path.resolve()),
        "-t", f"{clip_end - clip_start:.3f}",
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[a]",
        *ENCODE,
        str(out_path.resolve()),
    ]
    subprocess.run(cmd, check=True, cwd=workdir)


def render_thumbnail(seg: Segment, at_sec: float, out_path: Path, ass_path: Path,
                     crop: tuple[float, float, float] = (0.5, 0.5, 1.0),
                     pip: tuple[float, float, float] | None = None) -> None:
    """at_sec（元配信の秒）の1コマを、ショートと同じレイアウト＋サムネ文字でJPGにする。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{max(0.0, at_sec - seg.start_sec):.3f}",
        "-i", str(seg.path.resolve()),
        "-filter_complex", short_video_filter(*crop, ass_path.name, pip),
        "-map", "[v]", "-frames:v", "1", "-q:v", "3",
        str(out_path.resolve()),
    ]
    subprocess.run(cmd, check=True, cwd=ass_path.parent)
