"""処理済みVODの記録（state/processed.json、DESIGN.md 3.10）。判定はすべて純粋関数。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from kirinuki.config import REPO_ROOT
from kirinuki.twitch import Archive

STATE_PATH = REPO_ROOT / "state" / "processed.json"
UPLOADS_PATH = REPO_ROOT / "state" / "uploads.json"   # YouTubeに上げた時刻（1日の上限の管理）
SHORT_UPLOADS_PATH = REPO_ROOT / "state" / "uploads_shorts.json"   # ショートは別に数える（2026-11-03）
MAX_ATTEMPTS = 3
SETTLE = timedelta(minutes=5)  # 配信終了直後はアーカイブが確定していないので待つ


def load(path: Path = STATE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(state: dict, path: Path = STATE_PATH) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def needs_processing(entry: dict | None) -> bool:
    if entry is None:
        return True
    return entry.get("status") == "failed" and entry.get("attempts", 0) < MAX_ATTEMPTS


def pending(archives: list[Archive], state: dict, now: datetime, live: bool, max_age: timedelta) -> list[Archive]:
    """処理すべきVOD（新しい順。「最速」が売りなので一番新しい配信を先に）。配信中は何もしない。終了から10分以上・max_age以内のものだけ。"""
    if live:
        return []
    out = [
        a for a in archives
        if needs_processing(state.get(a.vod_id))
        and a.ended_at <= now - SETTLE
        and a.ended_at >= now - max_age
    ]
    return sorted(out, key=lambda a: a.created_at, reverse=True)


def record(state: dict, vod_id: str, status: str, now: datetime, **extra) -> dict:
    prev = state.get(vod_id, {})
    attempts = prev.get("attempts", 0) + (1 if status == "failed" else 0)
    state[vod_id] = {**prev, **extra, "status": status, "processed_at": now.isoformat(), "attempts": attempts}
    return state


def is_auth_error(message: str) -> bool:
    """サブスク限定などで取得できなかったか（エラー文から推定）。"""
    m = message.lower()
    return any(k in m for k in ("subscriber", "sub-only", "subscription", "unauthorized", "401", "403"))


def load_uploads(path: Path = UPLOADS_PATH) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def add_uploads(n: int, now: datetime, path: Path = UPLOADS_PATH) -> None:
    """n 本上げたことを記録する。2日より古い記録は消す。"""
    keep = [t for t in load_uploads(path) if datetime.fromisoformat(t) > now - timedelta(days=2)]
    path.write_text(json.dumps(keep + [now.isoformat()] * n, indent=1) + "\n", encoding="utf-8")


def recent_uploads(now: datetime, hours: float = 24, path: Path = UPLOADS_PATH) -> int:
    return sum(1 for t in load_uploads(path) if datetime.fromisoformat(t) > now - timedelta(hours=hours))


def is_short(filename: str) -> bool:
    return str(filename).startswith("shorts/")


def upload_budget(limit: int, short_limit: int, now: datetime, path: Path = UPLOADS_PATH,
                  short_path: Path = SHORT_UPLOADS_PATH) -> dict[str, int]:
    """直近24時間の分を差し引いた残り枠。長い動画（long）とショート（short）は別に数える。"""
    return {"long": max(0, limit - recent_uploads(now, path=path)),
            "short": max(0, short_limit - recent_uploads(now, path=short_path))}


def record_uploads(files, now: datetime, path: Path = UPLOADS_PATH, short_path: Path = SHORT_UPLOADS_PATH) -> None:
    files = list(files)
    shorts = sum(1 for f in files if is_short(f))
    if len(files) - shorts:
        add_uploads(len(files) - shorts, now, path)
    if shorts:
        add_uploads(shorts, now, short_path)
