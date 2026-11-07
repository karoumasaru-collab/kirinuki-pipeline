"""rclone で Googleドライブへ保存し、古いものを消す（DESIGN.md 3.8）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kirinuki.config import DriveConfig


def remote_root(cfg: DriveConfig) -> str:
    return f"{cfg.remote}:{cfg.root}"


def upload(local_dir: Path, folder: str, cfg: DriveConfig) -> str:
    dest = f"{remote_root(cfg)}/{folder}"
    subprocess.run(["rclone", "copy", str(local_dir), dest, "--stats-one-line", "-v"], check=True)
    return dest


def download(folder: str, files: list[str], dest: Path, cfg: DriveConfig) -> None:
    """ドライブの folder から files（相対パス）を dest に取り寄せる。
    ドライブには同じ名前のフォルダ（thumbs など）が2つできることがあり、copyto ではファイルが見つからない。
    そこで folder 全体を絞り込んで copy する（同じ名前のフォルダを両方見る。--update で新しい方が残る）。"""
    for f in files:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["rclone", "copy", f"{remote_root(cfg)}/{folder}", str(dest), "--include", "/" + f,
                        "--update"], check=True)
        if not (dest / f).exists():
            raise FileNotFoundError(f"ドライブに見つからない：{folder}/{f}")


def delete(folder: str, files: list[str], cfg: DriveConfig) -> None:
    """YouTube に上がった動画をドライブから消す（ゴミ箱も容量に数えられるので、ゴミ箱を経由しない）。"""
    for f in files:
        subprocess.run(["rclone", "deletefile", f"{remote_root(cfg)}/{folder}/{f}", "--drive-use-trash=false"],
                       check=False)


def cleanup(cfg: DriveConfig) -> None:
    """作成から retention_days を過ぎたファイルを、ゴミ箱を経由せずに消し、空のフォルダも消す。"""
    root = remote_root(cfg)
    subprocess.run(["rclone", "delete", root, "--min-age", f"{cfg.retention_days}d",
                    "--drive-use-trash=false", "-v"], check=True)
    subprocess.run(["rclone", "rmdirs", root, "--leave-root", "-v"], check=True)
