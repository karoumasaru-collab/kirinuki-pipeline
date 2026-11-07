"""チャットログの取得（TwitchDownloaderCLI）と読み込み。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Fragment:
    text: str
    is_emote: bool


@dataclass(frozen=True)
class ChatMessage:
    offset_sec: float
    body: str
    fragments: tuple[Fragment, ...]

    def text_without_emotes(self) -> str:
        if not self.fragments:
            return self.body
        return "".join(f.text for f in self.fragments if not f.is_emote)

    def is_emote_only(self) -> bool:
        return bool(self.fragments) and not self.text_without_emotes().strip()


@dataclass(frozen=True)
class GameChapter:
    start_sec: float
    end_sec: float
    game: str


@dataclass(frozen=True)
class VideoInfo:
    vod_id: str
    title: str
    created_at: str  # ISO 8601（UTC）
    length_sec: float
    game: str
    chapters: tuple[GameChapter, ...] = ()

    def game_at(self, sec: float) -> str:
        """その時刻にやっていたゲーム（Twitchのチャプター）。無ければ配信全体のゲーム。"""
        for c in self.chapters:
            if c.start_sec <= sec < c.end_sec and c.game:
                return c.game
        return self.game


@dataclass(frozen=True)
class Chat:
    video: VideoInfo
    messages: tuple[ChatMessage, ...]


def download_chat(vod_id: str, out_path: Path, cli: str = "TwitchDownloaderCLI") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [cli, "chatdownload", "--id", vod_id, "-o", str(out_path),
         "--compression", "None", "--collision", "Overwrite"],
        check=True,
    )


def parse_chat(raw: dict, vod_id: str = "") -> Chat:
    v = raw.get("video") or {}
    messages = []
    for c in raw.get("comments", []):
        m = c.get("message") or {}
        frags = tuple(
            Fragment(text=f.get("text") or "", is_emote=bool(f.get("emoticon")))
            for f in (m.get("fragments") or [])
        )
        messages.append(ChatMessage(
            offset_sec=float(c["content_offset_seconds"]),
            body=m.get("body") or "",
            fragments=frags,
        ))
    messages.sort(key=lambda x: x.offset_sec)
    length = float(v.get("length") or -1)
    if length <= 0:
        end = v.get("end")
        length = float(end) if end else (messages[-1].offset_sec if messages else 0.0)
    chapters = []
    for ch in v.get("chapters") or []:
        start = float(ch.get("startMilliseconds") or 0) / 1000
        dur = float(ch.get("lengthMilliseconds") or 0) / 1000
        game = ch.get("gameDisplayName") or ch.get("description") or ""
        if dur > 0:
            chapters.append(GameChapter(start, start + dur, game))
    video = VideoInfo(
        vod_id=str(v.get("id") or vod_id),
        title=v.get("title") or "",
        created_at=v.get("created_at") or "",
        length_sec=length,
        game=v.get("game") or "",
        chapters=tuple(chapters),
    )
    return Chat(video=video, messages=tuple(messages))


def load_chat(path: Path, vod_id: str = "") -> Chat:
    return parse_chat(json.loads(Path(path).read_text(encoding="utf-8")), vod_id)
