"""YouTube への非公開アップロード（DESIGN.md 3.9）。

Googleの重いライブラリは使わず、OAuth のトークン更新と resumable upload の REST を直接呼ぶ。
privacyStatus は youtube.privacy（CLAUDE.md 最重要ルール4）。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from kirinuki.chat import VideoInfo
from kirinuki.config import YouTubeConfig
from kirinuki.report import jst_date, twitch_time_link

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}"
API_URL = "https://www.googleapis.com/youtube/v3"
PLAYLIST_SUFFIX = "｜加藤純一 切り抜き"
TITLE_MAX = 100
CHANNEL_LINE = "加藤純一さんの配信から名場面を最速でお届けする、非公式の切り抜きチャンネルです。"


# ---------- 純粋関数（タイトル・概要欄） ----------

def clean(text: str) -> str:
    """YouTubeは < > を受け付けないので全角に置き換える。"""
    return text.replace("<", "＜").replace(">", "＞").strip()


def hashtag(word: str) -> str:
    return "#" + re.sub(r"[\s・:：'’\-]+", "", word)


def short_title(title: str, cfg: YouTubeConfig) -> str:
    suffix = cfg.title_suffix
    return clean(title)[: TITLE_MAX - len(suffix)] + suffix


def short_description(video: VideoInfo, start_sec: float, cfg: YouTubeConfig) -> str:
    game = video.game_at(start_sec)
    tags = [hashtag(t) for t in (*cfg.tags, game) if t]
    return clean("\n".join([
        f"▼元配信（{jst_date(video.created_at).replace('-', '/')}「{video.title}」）",
        twitch_time_link(video.vod_id, start_sec),
        "",
        CHANNEL_LINE,
        " ".join(dict.fromkeys(tags)),
    ]))


def digest_title(title: str, cfg: YouTubeConfig) -> str:
    """速報のタイトル。配信者名が入っていなければ末尾に付ける。"""
    t = clean(title)
    name = cfg.title_suffix.strip("【】")
    if name and name not in t:
        return t[: TITLE_MAX - len(cfg.title_suffix)] + cfg.title_suffix
    return t[:TITLE_MAX]


def digest_description(video: VideoInfo, digest, cfg: YouTubeConfig) -> str:
    from kirinuki.digest import description
    games = [video.game_at(i.sel.in_sec) for i in digest.items]
    tags = [hashtag(t) for t in (*cfg.tags, *games) if t]
    return description(video, digest, tags, CHANNEL_LINE)


def upload_md(video: VideoInfo, shorts: list, cfg: YouTubeConfig, uploaded: dict[str, str],
              digest_uploads=()) -> str:
    """手動アップロード用にコピーできる、速報・ショートごとのタイトル・概要欄。uploaded は {ファイル名: 動画ID}。"""
    lines = [f"# YouTube用テキスト：{video.title}", ""]
    if digest_uploads and isinstance(digest_uploads[0], str):  # 1本だけの古い呼び方
        digest_uploads = [digest_uploads]
    for filename, title, desc, _ in digest_uploads:
        vid = uploaded.get(filename)
        lines += [f"## {filename}（最新速報）", "",
                  f"YouTube：https://studio.youtube.com/video/{vid}/edit（{cfg.privacy}でアップロード済み）" if vid
                  else "YouTube：未アップロード", "",
                  "タイトル：", "```", title, "```", "概要欄：", "```", desc, "```", ""]
    for s in shorts:
        vid = uploaded.get(s.filename)
        lines += [
            f"## {s.filename}",
            "",
            f"YouTube：https://studio.youtube.com/video/{vid}/edit（{cfg.privacy}でアップロード済み）" if vid
            else "YouTube：未アップロード",
            "",
            "タイトル：",
            "```",
            short_title(s.title, cfg),
            "```",
            "概要欄：",
            "```",
            short_description(video, s.start_sec, cfg),
            "```",
            "",
        ]
    return "\n".join(lines)


# ---------- API ----------

@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str
    refresh_token: str

    @classmethod
    def from_env(cls) -> "Credentials | None":
        vals = [os.environ.get(k, "") for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")]
        return cls(*vals) if all(vals) else None


def access_token(c: Credentials) -> str:
    body = urllib.parse.urlencode({
        "client_id": c.client_id, "client_secret": c.client_secret,
        "refresh_token": c.refresh_token, "grant_type": "refresh_token",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=60) as r:
        return json.load(r)["access_token"]


def upload_video(token: str, path: Path, title: str, description: str, cfg: YouTubeConfig,
                 extra_tags: tuple[str, ...] = ()) -> str:
    """アップロードし、動画IDを返す（公開設定は youtube.privacy）。extra_tags はゲーム名など。"""
    meta = {
        "snippet": {
            "title": title, "description": description,
            "tags": list(dict.fromkeys(t for t in (*cfg.tags, *extra_tags) if t)),
            "categoryId": str(cfg.category_id), "defaultLanguage": "ja", "defaultAudioLanguage": "ja",
        },
        "status": {"privacyStatus": cfg.privacy if cfg.privacy in ("public", "private", "unlisted") else "private",
                   "selfDeclaredMadeForKids": False},
    }
    size = path.stat().st_size
    init = urllib.request.Request(UPLOAD_URL, data=json.dumps(meta).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size),
    })
    with urllib.request.urlopen(init, timeout=60) as r:
        location = r.headers["Location"]
    put = urllib.request.Request(location, data=path.read_bytes(), method="PUT", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "video/mp4", "Content-Length": str(size),
    })
    with urllib.request.urlopen(put, timeout=1800) as r:
        return json.load(r)["id"]


def set_thumbnail(token: str, video_id: str, jpg: Path) -> None:
    req = urllib.request.Request(THUMB_URL.format(vid=video_id), data=jpg.read_bytes(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "image/jpeg",
    })
    with urllib.request.urlopen(req, timeout=120):
        pass


def get_title(token: str, video_id: str) -> str:
    return _api(token, "GET", f"videos?part=snippet&id={video_id}")["items"][0]["snippet"]["title"]


def set_title(token: str, video_id: str, title: str) -> None:
    """公開済みの動画のタイトルだけ変える（説明・タグ・カテゴリは今のまま送り直す。送らないと消えるため）。"""
    sn = _api(token, "GET", f"videos?part=snippet&id={video_id}")["items"][0]["snippet"]
    keep = {k: sn[k] for k in ("description", "categoryId", "tags", "defaultLanguage", "defaultAudioLanguage") if k in sn}
    _api(token, "PUT", "videos?part=snippet", {"id": video_id, "snippet": {**keep, "title": title}})


def playlist_title(game: str) -> str:
    name = "雑談" if not game or game == "Just Chatting" else game   # Twitch は雑談を英語名で返す
    return f"{name}{PLAYLIST_SUFFIX}"


def _api(token: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"{API_URL}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def add_to_playlist(token: str, video_id: str, game: str, cache: dict[str, str]) -> str:
    """ゲームごとの再生リスト（なければ公開で作る）に動画を足し、再生リストIDを返す。"""
    title = playlist_title(game)
    if not cache:
        page = ""
        while True:
            res = _api(token, "GET", f"playlists?part=snippet&mine=true&maxResults=50{page}")
            cache.update({it["snippet"]["title"]: it["id"] for it in res.get("items", [])})
            if not res.get("nextPageToken"):
                break
            page = f"&pageToken={res['nextPageToken']}"
    if title not in cache:
        res = _api(token, "POST", "playlists?part=snippet,status", {
            "snippet": {"title": title, "description": f"加藤純一さんの{title.removesuffix(PLAYLIST_SUFFIX)}配信の切り抜きをまとめた再生リストです。",
                        "defaultLanguage": "ja"},
            "status": {"privacyStatus": "public"}})
        cache[title] = res["id"]
    body = {"snippet": {"playlistId": cache[title], "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    # 作ったばかりの再生リストや続けての追加は 409（Conflict）や 5xx が返ることがあるので、間をあけてやり直す
    for wait in (5, 15, 30, 0):
        try:
            _api(token, "POST", "playlistItems?part=snippet", body)
            return cache[title]
        except urllib.error.HTTPError as e:
            if not wait or (e.code != 409 and e.code < 500):
                raise
            time.sleep(wait)
    return cache[title]


def parse_upload_games(text: str) -> dict[str, str]:
    """upload.md から {ファイル名: ゲーム名} を取り出す（上げ直しで再生リストに入れる用）。"""
    out: dict[str, str] = {}
    for sec in text.split("\n## ")[1:]:
        filename = sec.split("\n", 1)[0].split("（")[0].strip()
        m = re.search(r"^ゲーム：(.*)$", sec.split("```")[0], re.M)
        if m:
            out[filename] = m.group(1).strip()
    return out


def parse_upload_md(text: str) -> dict[str, tuple[str, str]]:
    """upload.md から {ファイル名: (タイトル, 概要欄)} を取り出す（上げ直し用）。"""
    out: dict[str, tuple[str, str]] = {}
    for sec in text.split("\n## ")[1:]:
        filename = sec.split("\n", 1)[0].split("（")[0].strip()
        blocks = sec.split("```")
        if len(blocks) >= 4:
            out[filename] = (blocks[1].strip(), blocks[3].strip())
    return out


def is_limit_error(text: str) -> bool:
    return "uploadLimitExceeded" in text or "quotaExceeded" in text


def http_error_text(e: Exception) -> str:
    """HTTPエラーの本文（理由）を短く取り出す。トークンは含まれない。"""
    if isinstance(e, urllib.error.HTTPError):
        try:
            return f"{e.code} {e.read().decode(errors='replace')[:300]}"
        except Exception:  # noqa: BLE001
            return str(e.code)
    return str(e)
