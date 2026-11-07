"""Twitch Helix API による新着検知（DESIGN.md 3.1）。"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"


@dataclass(frozen=True)
class Archive:
    vod_id: str
    title: str
    created_at: datetime
    duration_sec: float

    @property
    def ended_at(self) -> datetime:
        return self.created_at + timedelta(seconds=self.duration_sec)


def parse_duration(s: str) -> float:
    """Twitchの "3h8m33s" 形式を秒にする。"""
    total = 0.0
    for num, unit in re.findall(r"(\d+)([hms])", s or ""):
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def parse_archive(d: dict) -> Archive:
    return Archive(
        vod_id=str(d["id"]),
        title=d.get("title", ""),
        created_at=datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")),
        duration_sec=parse_duration(d.get("duration", "")),
    )


class Helix:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.environ["TWITCH_CLIENT_ID"]
        secret = client_secret or os.environ["TWITCH_CLIENT_SECRET"]
        body = urllib.parse.urlencode({
            "client_id": self.client_id, "client_secret": secret, "grant_type": "client_credentials",
        }).encode()
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=30) as r:
            self.token = json.load(r)["access_token"]

    def _get(self, path: str, **params) -> list[dict]:
        url = f"{HELIX}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Client-Id": self.client_id, "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("data", [])

    def user_id(self, login: str) -> str:
        data = self._get("users", login=login)
        if not data:
            raise RuntimeError(f"Twitchユーザーが見つからない：{login}")
        return data[0]["id"]

    def is_live(self, user_id: str) -> bool:
        return bool(self._get("streams", user_id=user_id))

    def muted_segments(self, vod_id: str) -> list[tuple[float, float]]:
        """Twitch が著作権の音楽などで無音にした区間（開始秒, 終了秒）。"""
        data = self._get("videos", id=vod_id)
        segs = (data[0].get("muted_segments") or []) if data else []
        return [(float(m["offset"]), float(m["offset"]) + float(m["duration"])) for m in segs]

    def archives(self, user_id: str, first: int = 5) -> list[Archive]:
        return [parse_archive(d) for d in self._get("videos", user_id=user_id, type="archive", first=first)]
