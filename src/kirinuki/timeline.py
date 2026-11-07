"""配信全体の「チャットの流れ」を1分ごとにまとめる（DESIGN.md 9.2 計画）。純粋関数。

Claude① はこれを読んで、話題の区切りと動画の案を出す。
"""

from __future__ import annotations

from collections import Counter

from kirinuki.chat import ChatMessage, VideoInfo
from kirinuki.comments import normalize_for_ng
from kirinuki.peaks import normalize
from kirinuki.report import hms


def minute_lines(messages: list[ChatMessage], video: VideoInfo, ng_words: tuple[str, ...],
                 bucket_sec: int = 60, top_k: int = 5, max_chars: int = 16) -> list[str]:
    """「[0:42:00] 85件 ARC Raiders｜草×20 wwww×12 えぐい×5」の形の行を、bucket_sec ごとに返す。"""
    n = int(video.length_sec // bucket_sec) + 1
    counts = [0] * n
    texts: list[Counter] = [Counter() for _ in range(n)]
    first: list[dict] = [{} for _ in range(n)]
    for m in messages:
        i = int(m.offset_sec // bucket_sec)
        if not 0 <= i < n:
            continue
        counts[i] += 1
        body = m.body.strip()
        if not body or len(body) > max_chars or any(w in normalize_for_ng(body) for w in ng_words):
            continue
        key = normalize(body)
        texts[i][key] += 1
        first[i].setdefault(key, body)
    lines = []
    prev_game = None
    for i in range(n):
        t = i * bucket_sec
        game = video.game_at(t)
        g = f" 【{game}】" if game and game != prev_game else ""
        prev_game = game
        top = "　".join(f"{first[i][k]}×{c}" for k, c in texts[i].most_common(top_k) if c >= 2)
        lines.append(f"[{hms(t)}] {counts[i]}件{g}" + (f"｜{top}" if top else ""))
    return lines
