"""チャットの流速と反応語から山場を検出する（DESIGN.md 3.3）。

すべて純粋関数。入出力は ChatMessage の列と PeaksConfig だけ。
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass

from kirinuki.chat import ChatMessage
from kirinuki.config import PeaksConfig

# ロバストZスコアの分母の下限。チャットが静かな区間でMADが0になり、
# 数件の書き込みで巨大なZが出るのを防ぐ。
MIN_SCALE = 1.0
# 基準（中央値・MAD）を計算するのに最低限必要な過去の長さ。配信冒頭の誤検出を防ぐ。
MIN_HISTORY_SEC = 60.0


@dataclass(frozen=True)
class Candidate:
    id: int
    peak_sec: float
    start_sec: float
    end_sec: float
    score: float
    top_messages: tuple[tuple[str, int], ...]


def normalize(text: str) -> str:
    """弾幕判定・反応語判定用の正規化。全角半角をそろえ、同じ文字の4連続以上は3つに縮める。"""
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"\s+", "", t)
    return re.sub(r"(.)\1{3,}", r"\1\1\1", t)


def bin_scores(messages: list[ChatMessage], length_sec: float, cfg: PeaksConfig) -> list[float]:
    n = max(1, math.ceil(length_sec / cfg.bin_sec))
    scores = [0.0] * n
    bodies: list[Counter] = [Counter() for _ in range(n)]
    laugh = [normalize(w) for w in cfg.laugh_words]
    hype = [normalize(w) for w in cfg.hype_words]
    for m in messages:
        i = int(m.offset_sec // cfg.bin_sec)
        if not 0 <= i < n:
            continue
        key = normalize(m.body)
        s = 1.0
        if any(w and w in key for w in laugh):
            s += cfg.laugh_bonus
        if any(w and w in key for w in hype):
            s += cfg.hype_bonus
        scores[i] += s
        if key:
            bodies[i][key] += 1
    for i, counter in enumerate(bodies):
        for count in counter.values():
            if count >= cfg.spam_min_count:
                scores[i] += count * cfg.spam_per_msg
    return scores


def smooth(xs: list[float], k: int) -> list[float]:
    """中心の移動平均。端は使える範囲だけで平均する。"""
    if k <= 1:
        return list(xs)
    half = k // 2
    out = []
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        out.append(sum(xs[lo:hi]) / (hi - lo))
    return out


def robust_z(xs: list[float], window_bins: int, min_history_bins: int) -> list[float]:
    """直前 window_bins 個（自分は含まない）の中央値とMADで標準化する。"""
    z = [0.0] * len(xs)
    for i in range(len(xs)):
        hist = xs[max(0, i - window_bins):i]
        if len(hist) < min_history_bins:
            continue
        med = statistics.median(hist)
        mad = statistics.median(abs(x - med) for x in hist)
        z[i] = (xs[i] - med) / max(1.4826 * mad, MIN_SCALE)
    return z


def local_maxima(z: list[float], threshold: float) -> list[int]:
    """閾値以上の極大。平らな山は最初の点を採る。"""
    out = []
    for i, v in enumerate(z):
        if v < threshold:
            continue
        left = z[i - 1] if i > 0 else -math.inf
        right = z[i + 1] if i + 1 < len(z) else -math.inf
        if v > left and v >= right:
            out.append(i)
    return out


def merge_close(indices: list[int], z: list[float], merge_bins: int) -> list[int]:
    """merge_bins 以内に続く山をまとめ、各まとまりで最もZが高い点を残す。"""
    merged: list[list[int]] = []
    for i in sorted(indices):
        if merged and i - merged[-1][-1] <= merge_bins:
            merged[-1].append(i)
        else:
            merged.append([i])
    return [max(group, key=lambda j: z[j]) for group in merged]


def top_messages(messages: list[ChatMessage], start: float, end: float, k: int = 10) -> tuple[tuple[str, int], ...]:
    counts: Counter = Counter()
    first_form: dict[str, str] = {}
    for m in messages:
        if start <= m.offset_sec < end:
            key = normalize(m.body)
            if not key:
                continue
            counts[key] += 1
            first_form.setdefault(key, m.body.strip())
    return tuple((first_form[key], c) for key, c in counts.most_common(k))


def detect(messages: list[ChatMessage], length_sec: float, cfg: PeaksConfig) -> list[Candidate]:
    """山場候補をスコア上位 max_candidates 件まで返す（id は時刻順に1から）。"""
    raw = bin_scores(messages, length_sec, cfg)
    sm = smooth(raw, cfg.smooth_bins)
    z = robust_z(
        sm,
        window_bins=int(cfg.baseline_window_sec // cfg.bin_sec),
        min_history_bins=int(MIN_HISTORY_SEC // cfg.bin_sec),
    )
    peaks = merge_close(
        local_maxima(z, cfg.peak_threshold), z, int(cfg.merge_within_sec // cfg.bin_sec)
    )
    peaks = sorted(peaks, key=lambda i: z[i], reverse=True)[: cfg.max_candidates]
    peaks.sort()
    out = []
    for n, i in enumerate(peaks, start=1):
        peak_sec = i * cfg.bin_sec + cfg.bin_sec / 2
        start = max(0.0, peak_sec - cfg.lead_sec)
        end = min(length_sec, peak_sec + cfg.tail_sec)
        out.append(Candidate(
            id=n,
            peak_sec=peak_sec,
            start_sec=start,
            end_sec=end,
            score=round(z[i], 2),
            top_messages=top_messages(messages, start, end),
        ))
    return out
