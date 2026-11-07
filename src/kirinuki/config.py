"""config/channel.yaml の読み込み。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "channel.yaml"


@dataclass(frozen=True)
class PeaksConfig:
    bin_sec: float = 5
    smooth_bins: int = 3
    baseline_window_sec: float = 600
    peak_threshold: float = 4.0
    merge_within_sec: float = 60
    lead_sec: float = 45
    tail_sec: float = 15
    context_lead_sec: float = 90
    context_tail_sec: float = 30
    max_candidates: int = 20
    laugh_words: tuple[str, ...] = ()
    hype_words: tuple[str, ...] = ()
    laugh_bonus: float = 0.5
    hype_bonus: float = 0.5
    spam_per_msg: float = 0.3
    spam_min_count: int = 3


@dataclass(frozen=True)
class CommentsConfig:
    enabled: bool = True
    duration_sec: float = 4.0
    font: str = "Noto Sans CJK JP"
    font_size_short: int = 56
    font_size_digest: int = 52
    max_lanes_short: int = 8
    max_lanes_digest: int = 11
    max_chars: int = 30
    drop_emote_only: bool = True
    drop_urls: bool = True
    ng_words_file: str = "config/ng_words.txt"


@dataclass(frozen=True)
class OutputConfig:
    shorts_per_vod: int = 8          # 上限
    shorts_min: int = 3
    short_min_quality: int = 6
    transcribe_top: int = 30
    short_max_sec: float = 60
    digest_min_sec: float = 300
    digest_max_sec: float = 3600
    digests_max: int = 3
    digest_max_short_scenes: int = 2
    roadshow_part_sec: float = 7200
    roadshow: bool = True
    singles_max: int = 3
    topic_max_sec: float = 3600      # 単体動画の長さの上限。超えたら山場の多い区間に切り詰める
    prefer: tuple[str, ...] = ()     # 先に回す題材（ゲーム名かタイトルに含む言葉）
    avoid: tuple[str, ...] = ()      # 後回しにする題材（面白さ9以上はふつうの扱い）
    avoid_max_sec: float = 1200      # avoid の題材は1本20分まで（1つの事件に絞る）
    normal_max_sec: float = 1200     # prefer でも avoid でもない題材は1本20分まで
    english_subs_games: tuple[str, ...] = ()   # 英語の会話に日本語訳の字幕を付けるゲーム（DESIGN.md 9.6）
    pending_max_hours: float = 48
    title_band: bool = False


@dataclass(frozen=True)
class DriveConfig:
    remote: str = "gdrive"
    root: str = "kirinuki"
    retention_days: int = 7


@dataclass(frozen=True)
class YouTubeConfig:
    enabled: bool = False
    daily_upload_limit: int = 6
    short_daily_limit: int = 2       # ショートは長い動画の枠とは別に数える（2026-11-03）
    privacy: str = "private"
    title_suffix: str = ""
    category_id: int = 20
    tags: tuple[str, ...] = ()
    playlists: bool = True          # ゲームごとの再生リストに入れる（許可 youtube が必要）


@dataclass(frozen=True)
class AutoConfig:
    max_age_hours: float = 48       # これより前に終わった配信は自動では処理しない
    max_vods_per_run: int = 2       # 1回の定期実行で処理する本数の上限


@dataclass(frozen=True)
class WhisperConfig:
    model: str = "small"
    compute_type: str = "int8"


@dataclass(frozen=True)
class Config:
    channel_login: str
    model: str
    peaks: PeaksConfig = field(default_factory=PeaksConfig)
    comments: CommentsConfig = field(default_factory=CommentsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    drive: DriveConfig = field(default_factory=DriveConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    auto: AutoConfig = field(default_factory=AutoConfig)

    def ng_words_path(self) -> Path:
        return REPO_ROOT / self.comments.ng_words_file


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    p = dict(raw.get("peaks", {}))
    words = p.pop("reaction_words", {})
    bonus = p.pop("bonus", {})
    peaks = PeaksConfig(
        **p,
        laugh_words=tuple(words.get("laugh", ())),
        hype_words=tuple(words.get("hype", ())),
        laugh_bonus=bonus.get("laugh", 0.5),
        hype_bonus=bonus.get("hype", 0.5),
        spam_per_msg=bonus.get("spam_per_msg", 0.3),
        spam_min_count=bonus.get("spam_min_count", 3),
    )
    return Config(
        channel_login=raw["channel_login"],
        model=raw["model"],
        peaks=peaks,
        comments=CommentsConfig(**raw.get("comments", {})),
        output=OutputConfig(**{**raw.get("output", {}),
                                 **{k: tuple(raw.get("output", {}).get(k) or ()) for k in ("prefer", "avoid", "english_subs_games")}}),
        drive=DriveConfig(**raw.get("drive", {})),
        whisper=WhisperConfig(**raw.get("whisper", {})),
        youtube=YouTubeConfig(**{**raw.get("youtube", {}), "tags": tuple(raw.get("youtube", {}).get("tags", ()))}),
        auto=AutoConfig(**raw.get("auto", {})),
    )
