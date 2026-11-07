from pathlib import Path

from kirinuki.chat import ChatMessage, load_chat
from kirinuki.config import PeaksConfig, load_config
from kirinuki.peaks import (
    bin_scores, detect, local_maxima, merge_close, normalize, robust_z, smooth, top_messages,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chat_small.json"


def msg(t, body):
    return ChatMessage(offset_sec=t, body=body, fragments=())


def test_normalize_collapses_repeats_and_width():
    assert normalize("ｗｗｗｗｗｗ") == "www"
    assert normalize("wwwww") == "www"
    assert normalize(" 草 ") == "草"


def test_bin_scores_bonus_and_spam():
    cfg = PeaksConfig(bin_sec=5, laugh_words=("草",), hype_words=("神",),
                      laugh_bonus=0.5, hype_bonus=0.5, spam_per_msg=0.3, spam_min_count=3)
    msgs = [msg(1, "草"), msg(2, "草"), msg(3, "草"), msg(6, "神"), msg(7, "こんにちは")]
    s = bin_scores(msgs, 10, cfg)
    # ビン0：草×3 → 1.5×3 ＋ 弾幕 3×0.3
    assert s[0] == 4.5 + 0.9
    # ビン1：神(1.5) ＋ こんにちは(1.0)、弾幕なし
    assert s[1] == 2.5


def test_smooth_centered():
    assert smooth([0, 3, 0], 3) == [1.5, 1.0, 1.5]
    assert smooth([1, 2], 1) == [1, 2]


def test_robust_z_needs_history():
    z = robust_z([1.0] * 5 + [10.0], window_bins=10, min_history_bins=5)
    assert z[:5] == [0.0] * 5
    assert z[5] == 9.0  # MAD=0 なので分母は下限の1.0


def test_local_maxima_plateau_takes_first():
    assert local_maxima([0, 5, 5, 0, 6, 1], 4) == [1, 4]


def test_merge_close_keeps_highest():
    z = [0] * 30
    z[10], z[15], z[29] = 5, 7, 6
    assert merge_close([10, 15, 29], z, merge_bins=12) == [15, 29]


def test_top_messages_counts_in_window():
    msgs = [msg(1, "草"), msg(2, "ｗｗｗ"), msg(3, "www"), msg(20, "草")]
    assert top_messages(msgs, 0, 10) == (("ｗｗｗ", 2), ("草", 1))


def test_detect_on_fixture():
    cfg = load_config().peaks
    chat = load_chat(FIXTURE)
    cands = detect(list(chat.messages), chat.video.length_sec, cfg)
    # 600秒の弾幕と1200秒の盛り上がりの2つ。630秒の小山は600秒に統合される
    assert [c.id for c in cands] == [1, 2]
    assert 595 <= cands[0].peak_sec <= 615
    assert 1195 <= cands[1].peak_sec <= 1215
    assert cands[0].score > cands[1].score
    for c in cands:
        assert c.end_sec - c.start_sec == cfg.lead_sec + cfg.tail_sec
        assert c.start_sec < c.peak_sec < c.end_sec


def test_detect_respects_max_candidates_and_clamps():
    cfg = PeaksConfig(bin_sec=5, smooth_bins=1, baseline_window_sec=600, peak_threshold=4,
                      merge_within_sec=60, lead_sec=45, tail_sec=15, max_candidates=1)
    msgs = [msg(t, "x") for t in range(0, 300, 5)]
    msgs += [msg(100 + i * 0.1, "a") for i in range(30)]
    msgs += [msg(250 + i * 0.1, "b") for i in range(10)]
    cands = detect(msgs, 300, cfg)
    assert len(cands) == 1
    assert 100 <= cands[0].peak_sec <= 105
