"""第2版の処理（DESIGN.md 9.2）：計画 → 書き出し（複数台で同時）→ 仕上げ。

- plan：チャット・山場・話題の区切り・各動画の仕上げを決めて plan.json にする（1台）
- render_item：plan.json の動画1本を書き出し、ドライブと YouTube に上げて結果を JSON にする（1本1台）
- finalize：結果をまとめて upload.md などをドライブに保存し、処理済みの記録を更新する（1台）
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from kirinuki import chat as chatmod
from kirinuki import comments, digest as digestmod, peaks, pipeline, publish_drive, publish_youtube, select, thumb2
from kirinuki import overlay, timeline, topics, twitch
from kirinuki.chat import GameChapter, VideoInfo
from kirinuki.config import Config
from kirinuki.fetch import fetch_audio, fetch_frame, fetch_segment, vod_url
from kirinuki.peaks import Candidate
from kirinuki.report import JST, ShortResult, hms, jst_date, twitch_time_link
from kirinuki.transcribe import transcribe_all

log = pipeline.log
BOUNDARY_BEFORE, BOUNDARY_AFTER = 60.0, 30.0   # 区切りの前後に文字起こしする範囲


# ---------- 読み書き ----------

def video_from(d: dict) -> VideoInfo:
    return VideoInfo(**{**d, "chapters": tuple(GameChapter(**c) for c in d.get("chapters", []))})


def sel_from(d: dict) -> select.Selection:
    return select.Selection(**{**d, "band": tuple(d["band"]), "crop": select.Crop(**d["crop"])})


def topic_from(d: dict) -> topics.TopicVideo:
    return topics.TopicVideo(**{**d, "face": tuple(d["face"]) if d.get("face") else None,
                                "chapters": tuple(tuple(c) for c in d["chapters"]), "bubbles": tuple(d["bubbles"])})


def cand_from(d: dict) -> Candidate:
    return Candidate(**{**d, "top_messages": tuple(tuple(x) for x in d["top_messages"])})


def work_dir(workdir: Path, vod_id: str) -> Path:
    return workdir / vod_id


# ---------- 説明文 ----------

def topic_description(video: VideoInfo, v: topics.TopicVideo, tags: tuple[str, ...]) -> str:
    lines = []
    if v.game:
        lines.append(f"{v.game}。" + v.description if not v.description.startswith(v.game) else v.description)
    else:
        lines.append(v.description)
    lines += ["", "▼チャプター"]
    lines += [f"{digestmod.timestamp(s - v.in_sec)} {label}" for s, label in v.chapters]
    lines += ["", f"▼元配信（{jst_date(video.created_at).replace('-', '/')}「{video.title}」）",
              twitch_time_link(video.vod_id, v.in_sec), "", publish_youtube.CHANNEL_LINE,
              " ".join(dict.fromkeys(publish_youtube.hashtag(t) for t in (*tags, v.game) if t))]
    return "\n".join(lines).replace("<", "＜").replace(">", "＞").strip()


# ---------- ロードショー型ダイジェスト（DESIGN.md 10） ----------

CHAT_GAMES = ("Just Chatting", "雑談", "")


def strip_date(title: str) -> str:
    import re
    return re.sub(r"【\d{4}/\d{2}/\d{2}[^】]*】\s*$", "", title).strip()


def split_parts(vids: list[topics.TopicVideo], part_sec: float) -> list[list[topics.TopicVideo]]:
    """時系列の話題を、1本 part_sec 以内になるよう前から順に分ける。"""
    parts: list[list[topics.TopicVideo]] = [[]]
    total = 0.0
    for v in sorted(vids, key=lambda v: v.in_sec):
        dur = v.out_sec - v.in_sec
        if parts[-1] and total + dur > part_sec:
            parts.append([])
            total = 0.0
        parts[-1].append(v)
        total += dur
    return [p for p in parts if p]


def roadshow_title(video: VideoInfo, part: list[topics.TopicVideo], k: int, n: int) -> str:
    durs: dict[str, float] = {}
    for v in part:
        g = "雑談" if v.game in CHAT_GAMES else v.game
        durs[g] = durs.get(g, 0) + v.out_sec - v.in_sec
    games = [g for g, _ in sorted(durs.items(), key=lambda x: -x[1])][:2]
    name = "加藤純一 雑談ダイジェスト" if games == ["雑談"] else f"加藤純一の{'・'.join(g for g in games if g != '雑談') or '雑談'}ダイジェスト"
    best = max(part, key=lambda v: v.quality)
    hook = best.thumb_text or strip_date(best.title)[:20]
    tag = topics.date_tag(video)
    if n > 1:
        tag = tag[:-1] + ("前編" if k == 1 else "後編" if k == n else "中編") + "】"
    return f"{name}　{hook}{tag}"[:100]


def roadshow_description(video: VideoInfo, part: list[topics.TopicVideo], tags: tuple[str, ...]) -> str:
    lines = ["▼チャプター"]
    t = 0.0
    for v in part:
        lines.append(f"{digestmod.timestamp(t)} {strip_date(v.title)[:40]}")
        t += v.out_sec - v.in_sec
    lines += ["", "▼内容"]
    lines += [f"・{strip_date(v.title)[:40]}：{v.description}" for v in part if v.description]
    lines += ["", f"▼元配信（{jst_date(video.created_at).replace('-', '/')}「{video.title}」）"]
    lines += [f"・{strip_date(v.title)[:30]}：{twitch_time_link(video.vod_id, v.in_sec)}" for v in part]
    games = [v.game for v in part if v.game and v.game not in CHAT_GAMES]
    lines += ["", publish_youtube.CHANNEL_LINE,
              " ".join(dict.fromkeys(publish_youtube.hashtag(x) for x in (*tags, *games) if x))]
    return "\n".join(lines).replace("<", "＜").replace(">", "＞").strip()


# ---------- 計画 ----------

def plan(vod_id: str, cfg: Config, workdir: Path) -> dict:
    work = work_dir(workdir, vod_id)
    work.mkdir(parents=True, exist_ok=True)
    chat_path = work / "chat.json"
    if not chat_path.exists():
        log("チャットを取得中")
        chatmod.download_chat(vod_id, chat_path)
    chat = chatmod.load_chat(chat_path, vod_id)
    video, msgs = chat.video, list(chat.messages)
    log(f"「{video.title}」 長さ {video.length_sec:.0f}秒 コメント {len(msgs)}件")
    ng_words = comments.load_ng_words(cfg.ng_words_path())
    cands = peaks.detect(msgs, video.length_sec, cfg.peaks)
    log(f"山場候補 {len(cands)}件")
    usages = []

    # ショートと名場面まとめ（第1版の選定をそのまま使う）
    transcripts, frames, fetched = pipeline.gather_materials(vod_id, cands, video.length_sec, work, cfg)
    sels, usage1, method, raw_digests = pipeline.decide(video, cands, transcripts, frames, fetched, ng_words, cfg,
                                                        cfg.output.shorts_per_vod)
    if usage1:
        usages.append(asdict(usage1))
    o = cfg.output
    # 手動のやり直しで本数を変える（run.yml の入力 singles・shorts・prefer_game から）
    if os.environ.get("KIRINUKI_SINGLES") or os.environ.get("KIRINUKI_SHORTS"):
        o = replace(o, singles_max=int(os.environ.get("KIRINUKI_SINGLES") or o.singles_max),
                    shorts_per_vod=int(os.environ.get("KIRINUKI_SHORTS") or o.shorts_per_vod))
    prefer = os.environ.get("KIRINUKI_PREFER_GAME", "") or o.prefer   # 手動の指定があればそちら
    shorts = select.choose_shorts(sels, cands, o.shorts_per_vod, min(o.shorts_min, o.shorts_per_vod),
                                  o.short_min_quality)
    # Twitch が著作権の音楽などで音を消した区間は使わない（ALGS の同時視聴などで起きる）
    muted: list[tuple[float, float]] = []
    try:
        muted = twitch.Helix().muted_segments(vod_id)
    except Exception as e:  # noqa: BLE001
        log(f"無音区間の取得に失敗（{e}）。無音の確認なしで続ける")
    if muted:
        log("無音区間：" + "、".join(f"{hms(a)}〜{hms(b)}" for a, b in muted))
        shorts = [s for s in shorts if not topics.muted_in(s.in_sec, s.out_sec, muted)]

    # 話題の区切り（Claude①）
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    drafts: list[topics.TopicDraft] = []
    if has_key:
        try:
            lines = timeline.minute_lines(msgs, video, ng_words)
            raw, u = select.call_claude(cfg.model, topics.plan_content(
                topics.PLAN_PROMPT.read_text(encoding="utf-8"), video, lines), topics.PLAN_SCHEMA)
            usages.append(asdict(u))
            drafts = topics.normalize_plan(raw, video.length_sec)
            log(f"Claude①（{u.model}）入力 {u.input_tokens}・出力 {u.output_tokens}トークン：話題 {len(drafts)}本")
        except Exception as e:  # noqa: BLE001
            log(f"話題の区切りに失敗したので、山場の前後で代用（{e}）")
    if not drafts:
        drafts = topics.fallback_drafts(cands, video.length_sec)
    if muted:
        n = len(drafts)
        drafts = topics.avoid_muted_drafts(drafts, muted)
        if len(drafts) < n:
            log(f"無音区間が長い話題 {n - len(drafts)}本を外した")

    # 区切りの前後の文字起こしと、代表的な画面（同時に取得）
    audio, frames2 = {}, {}

    def one(d: topics.TopicDraft) -> None:
        try:
            audio[("s", d.id)] = fetch_audio(vod_id, max(0, d.start_sec - BOUNDARY_BEFORE), d.start_sec + BOUNDARY_AFTER,
                                              work / "audio_t", f"s_{d.id:02d}", video.length_sec)
            audio[("e", d.id)] = fetch_audio(vod_id, max(0, d.end_sec - BOUNDARY_AFTER), d.end_sec + BOUNDARY_BEFORE,
                                              work / "audio_t", f"e_{d.id:02d}", video.length_sec)
        except Exception as e:  # noqa: BLE001
            log(f"話題{d.id}: 音声の取得に失敗（{e}）")
        inside = [c for c in cands if d.start_sec <= c.peak_sec < d.end_sec]
        at = max(inside, key=lambda c: c.score).peak_sec if inside else (d.start_sec + d.end_sec) / 2
        try:
            frames2[d.id] = fetch_frame(vod_id, at, work / "frames_t", f"t_{d.id:02d}")
        except Exception as e:  # noqa: BLE001
            log(f"話題{d.id}: 静止画の取得に失敗（{e}）")

    with ThreadPoolExecutor(pipeline.DOWNLOAD_WORKERS) as ex:
        list(ex.map(one, drafts))
    lines_by = {}
    if audio:
        try:
            lines_by = transcribe_all(audio, cfg.whisper.model, cfg.whisper.compute_type)
        except Exception as e:  # noqa: BLE001
            log(f"区切りの文字起こしに失敗（{e}）")
    start_t = {i: v for (k, i), v in lines_by.items() if k == "s"}
    end_t = {i: v for (k, i), v in lines_by.items() if k == "e"}

    # 各動画の仕上げ（Claude②）
    vids: list[topics.TopicVideo] = []
    if has_key:
        try:
            content = topics.final_content(topics.FINAL_PROMPT.read_text(encoding="utf-8"), video, drafts,
                                           start_t, end_t, cands, frames2, transcripts, muted)
            raw, u = select.call_claude(cfg.model, content, topics.FINAL_SCHEMA)
            usages.append(asdict(u))
            vids = topics.normalize_final(raw, drafts, video)
            log(f"Claude②（{u.model}）入力 {u.input_tokens}・出力 {u.output_tokens}トークン")
        except Exception as e:  # noqa: BLE001
            log(f"話題動画の仕上げに失敗したので、区切りどおりで代用（{e}）")
    if not vids:
        vids = topics.normalize_final({}, drafts, video)
    if muted:
        vids = [w for w in (topics.avoid_muted_video(v, muted) for v in vids) if w]
    vids = [topics.game_from_title(v) for v in vids]
    # 長すぎる話題は、山場の多い区間に切り詰める（2026-11-02 オーナー判断：95分・100分の回は長すぎる。ARC は20分まで）
    scores = [(c.peak_sec, c.score) for c in cands]
    vids = [topics.cap_length(v, scores, topics.max_length(v, o)) for v in vids]

    # タイトルの点検（Claude③）：意味が通らない・中身と合わないタイトルを直す
    if has_key and vids and topics.CHECK_PROMPT.exists():
        try:
            content = topics.check_content(topics.CHECK_PROMPT.read_text(encoding="utf-8"), video, vids, drafts,
                                           start_t, end_t, cands, transcripts)
            raw, u = select.call_claude(cfg.model, content, topics.CHECK_SCHEMA)
            usages.append(asdict(u))
            vids, notes = topics.apply_checked(raw, vids, video)
            log(f"Claude③（{u.model}）タイトルの点検：{len(notes)}本を直した")
            for n in notes:
                log(f"  {n}")
        except Exception as e:  # noqa: BLE001
            log(f"タイトルの点検に失敗したので、そのまま使う（{e}）")

    # 主役：ロードショー型ダイジェスト（話題を時系列でつなぐ。2時間を超えたら分ける）
    parts = split_parts(vids, o.roadshow_part_sec) if o.roadshow else []  # output.roadshow: false で作らない
    items = [{"id": f"roadshow_{k}", "kind": "roadshow", "quality": 10,
              "title": roadshow_title(video, part, k, len(parts)),
              "description": roadshow_description(video, part, cfg.youtube.tags),
              "topics": [asdict(v) for v in part]} for k, part in enumerate(parts, start=1)]
    # 入口：面白い話題は単体でも出す（優先の題材 → ふつう → 後回しの題材、同じ段は面白さの順。2026-11-01）
    singles = sorted(vids, key=lambda v: topics.rank(v, prefer, o.avoid), reverse=True)[: o.singles_max]
    items += [{"id": f"topic_{v.topic_id:02d}", "kind": "topic", "quality": v.quality, "video": asdict(v)}
              for v in singles]
    items += [{"id": f"short_{k:02d}", "kind": "short", "quality": s.quality, "sel": asdict(s)}
              for k, s in enumerate(shorts, start=1)]
    from kirinuki import state as st_mod
    budget = st_mod.upload_budget(cfg.youtube.daily_upload_limit, cfg.youtube.short_daily_limit, datetime.now(JST))
    log(f"YouTubeの残り枠：長い動画 {budget['long']}本・ショート {budget['short']}本（直近24時間の分を差し引き）")
    for it in items:
        kind = "short" if it["kind"] == "short" else "long"
        it["youtube"] = budget[kind] > 0
        budget[kind] -= 1
    result = {
        "vod_id": vod_id, "video": asdict(video), "cands": [asdict(c) for c in cands],
        "items": items, "usage": usages, "method": method,
        "topics": [asdict(v) for v in vids],   # 選ばなかった話題も残す（あとで足すとき用）
        "drive_folder": f"{jst_date(video.created_at)}_{vod_id}",
        "generated_at": datetime.now(JST).isoformat(),
    }
    (work / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"計画：ダイジェスト {len(parts)}本（話題 {len(vids)}個）・単体 {len(singles)}本・ショート {len(shorts)}本")
    for v in vids:
        log(f"  {hms(v.in_sec)}〜{hms(v.out_sec)}（{(v.out_sec - v.in_sec) / 60:.0f}分）「{v.title}」")
    return result


def live_now(cfg: Config) -> bool:
    """加藤さんが配信中か。配信中は投稿しない（CLAUDE.md ルール8）。確かめられないときも配信中として扱う。"""
    try:
        h = twitch.Helix()
        return h.is_live(h.user_id(cfg.channel_login))
    except Exception as e:  # noqa: BLE001
        log(f"配信中かどうかを確かめられない（{e}）。投稿は次の回に回す")
        return True


# ---------- 足し算（作った動画はそのままで、話題を足す・取りやめる） ----------

def add_plan(vod_id: str, cfg: Config, workdir: Path, add_ids: list[int], drop: list[str], st: dict) -> dict:
    """前回の記録（ドライブの metadata.json）から、足す話題だけの計画を作る。Claude は呼ばない。"""
    entry = st.get(vod_id) or {}
    if not entry.get("drive_folder"):
        raise RuntimeError(f"{vod_id} は処理の記録がない")
    work = work_dir(workdir, vod_id)
    work.mkdir(parents=True, exist_ok=True)
    publish_drive.download(entry["drive_folder"], ["metadata.json"], work / "prev", cfg.drive)
    meta = json.loads((work / "prev" / "metadata.json").read_text(encoding="utf-8"))
    by_id = {t["topic_id"]: t for t in meta.get("topics", [])}
    if not by_id:
        raise RuntimeError("この配信の記録には話題が残っていない（2026-11-01 より前の処理）")
    have = {r.get("file") for r in meta.get("results", [])} - set(drop)   # 取りやめた分は作り直せる
    scores = [(c["peak_sec"], c["score"]) for c in meta.get("cands", [])]
    items = []
    for i in add_ids:
        if i not in by_id:
            raise RuntimeError(f"話題{i}は無い（あるのは {sorted(by_id)}）")
        if f"topic_{i:02d}.mp4" in have:
            log(f"話題{i}はもう作ってあるので飛ばす")
            continue
        v = topic_from(by_id[i])
        t = asdict(topics.cap_length(v, scores, topics.max_length(v, cfg.output)))
        items.append({"id": f"topic_{i:02d}", "kind": "topic", "quality": t.get("quality", 0), "video": t,
                      "youtube": False})   # 上げるのは枠を見て上げ直しの仕組みで
        log(f"足す：topic_{i:02d}「{t['title']}」")
    for f in drop:
        log(f"取りやめる：{f}")
    if not (work / "chat.json").exists():
        log("チャットを取得中")
        chatmod.download_chat(vod_id, work / "chat.json")
    p = {k: v for k, v in meta.items() if k != "results"}
    p.update(items=items, mode="add", drop=drop, prev_results=[r for r in meta.get("results", [])
                                                              if r.get("file") not in drop])
    (work / "plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def merge_add(entry: dict, drop: list[str], new: list[dict]) -> dict:
    """足し算のあとの記録。上げ直し待ちは前回の分（取りやめを除く）＋足した分。"""
    old = entry.get("youtube_pending", [])
    return {
        "youtube_pending": [f for f in old if f not in drop] + [r["file"] for r in new if r.get("pending")],
        "youtube_dropped": entry.get("youtube_dropped", []) + [f for f in drop if f in old],
        "youtube_ids": entry.get("youtube_ids", []) + [r["youtube_id"] for r in new if r.get("youtube_id")],
        "files": [f for f in entry.get("files", []) if f not in drop] + [r["file"] for r in new if r.get("file")],
    }


# ---------- 書き出し ----------

def render_item(vod_id: str, item_id: str, cfg: Config, workdir: Path, youtube: bool) -> dict:
    work = work_dir(workdir, vod_id)
    p = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    item = next(i for i in p["items"] if i["id"] == item_id)
    video = video_from(p["video"])
    msgs = list(chatmod.load_chat(work / "chat.json", vod_id).messages)
    ng_words = comments.load_ng_words(cfg.ng_words_path())
    cands = [cand_from(c) for c in p["cands"]]
    peak_of = {c.id: c.peak_sec for c in cands}
    out = work / f"out_{item_id}"
    (out / "thumbs").mkdir(parents=True, exist_ok=True)
    tags = cfg.youtube.tags
    res = {"id": item_id, "kind": item["kind"], "quality": item.get("quality", 0)}

    if item["kind"] == "topic":
        v = topic_from(item["video"])
        log(f"{item_id}: {hms(v.in_sec)}〜{hms(v.out_sec)}「{v.title}」を取得中")
        seg = fetch_segment(vod_id, v.in_sec, v.out_sec, work / "segments", f"seg_{item_id}", video.length_sec)
        ass = None
        sub_styles, sub_events, title, badge = [], [], v.title, ""
        if cfg.output.english_subs_games and topics.prefers(v, cfg.output.english_subs_games):
            # 英語の会話に日本語訳の字幕（DESIGN.md 9.6）。失敗しても字幕なしで作る
            try:
                from kirinuki import subs
                en = subs.merge_lines(subs.english_lines(seg, v.in_sec, v.out_sec, subs.WHISPER_MODEL,
                                                         cfg.whisper.compute_type))
                pairs = subs.translate(en, v.game)
                sub_styles, sub_events = [subs.style()], subs.events(pairs, v.in_sec)
                title = subs.mark_title(v.title, len(pairs))
                badge = subs.THUMB_BADGE if title != v.title else ""
                log(f"{item_id}: 英語 {len(en)}行 → 日本語字幕 {len(pairs)}行（区間の頭は元配信 {seg.start_sec:.1f}秒）")
                for x, ja in pairs:   # 訳が正しいかをあとで確かめられるように、全部ログに出す
                    log(f"{item_id}: 字幕 {hms(x.start_sec - v.in_sec)} {x.text} → {ja}")
            except Exception as e:  # noqa: BLE001
                log(f"{item_id}: 英語の字幕を作れなかった（{e}）")
        if cfg.comments.enabled or sub_events:
            layout = comments.digest_layout(cfg.comments)
            text = (comments.clip_comments(msgs, v.in_sec, v.out_sec, ng_words, cfg.comments, layout,
                                           sub_styles, sub_events).ass if cfg.comments.enabled
                    else overlay.ass_document(layout.play_w, layout.play_h, sub_styles, sub_events))
            ass = work / "segments" / f"{item_id}.ass"
            ass.write_text(text, encoding="utf-8")
        mp4 = out / f"{item_id}.mp4"
        digestmod.render_part(seg, v.in_sec, v.out_sec, mp4, ass)
        log(f"{item_id}: 書き出し完了 {(v.out_sec - v.in_sec) / 60:.1f}分 {mp4.stat().st_size / 1e6:.0f}MB")
        thumb = f"thumbs/{item_id}.jpg"
        try:
            ok = thumb2.render(seg, v.thumb_sec, v.thumb_text, v.game, v.face, v.bubbles, out / thumb, v.thumb_emph, badge)
            log(f"{item_id}: サムネ作成（顔の切り抜き {'成功' if ok else 'なし'}）")
        except Exception as e:  # noqa: BLE001
            log(f"{item_id}: サムネの作成に失敗（{e}）")
            thumb = ""
        res.update(file=mp4.name, title=title, description=topic_description(video, v, tags), thumb=thumb,
                   minutes=round((v.out_sec - v.in_sec) / 60, 1), game=v.game)

    elif item["kind"] == "short":
        s = sel_from(item["sel"])
        seg = fetch_segment(vod_id, s.in_sec, s.out_sec, work / "segments", f"seg_{item_id}", video.length_sec)
        r: ShortResult = pipeline.make_short(s, item_id, seg, msgs, ng_words, cfg, out, work, peak_of.get(s.cand_id))
        res.update(file=r.filename, title=publish_youtube.short_title(r.title, cfg.youtube),
                   description=publish_youtube.short_description(video, r.start_sec, cfg.youtube), thumb=r.thumb,
                   game=video.game_at(s.in_sec))

    elif item["kind"] == "roadshow":
        part = [topic_from(d) for d in item["topics"]]
        best = max(part, key=lambda v: v.quality)
        files = []
        layout = comments.digest_layout(cfg.comments)
        thumb = f"thumbs/{item_id}.jpg"
        # サーバーの容量（約14GB）を超えないよう、話題を1つずつ取得→書き出し→削除する
        for k, v in enumerate(part, start=1):
            seg = fetch_segment(vod_id, v.in_sec, v.out_sec, work / "segments", f"seg_r{v.topic_id:02d}",
                                video.length_sec)
            ass = None
            if cfg.comments.enabled:
                cc = comments.clip_comments(msgs, v.in_sec, v.out_sec, ng_words, cfg.comments, layout)
                ass = work / "segments" / f"{item_id}_{k:02d}.ass"
                ass.write_text(cc.ass, encoding="utf-8")
            f = work / f"parts_{item_id}" / f"p{k:02d}.mp4"
            digestmod.render_part(seg, v.in_sec, v.out_sec, f, ass)
            files.append(f)
            log(f"{item_id}: {k}/{len(part)}「{strip_date(v.title)}」{(v.out_sec - v.in_sec) / 60:.0f}分")
            if v.topic_id == best.topic_id:
                try:
                    thumb2.render(seg, best.thumb_sec, best.thumb_text, best.game, best.face, best.bubbles, out / thumb)
                except Exception as e:  # noqa: BLE001
                    log(f"{item_id}: サムネの作成に失敗（{e}）")
                    thumb = ""
            seg.path.unlink(missing_ok=True)
        mp4 = out / f"{item_id}.mp4"
        digestmod.concat(files, mp4)
        for f in files:
            f.unlink(missing_ok=True)
        minutes = sum(v.out_sec - v.in_sec for v in part) / 60
        log(f"{item_id}: 書き出し完了 {minutes:.0f}分 {mp4.stat().st_size / 1e6:.0f}MB")
        res.update(file=mp4.name, title=item["title"], description=item["description"], thumb=thumb,
                   minutes=round(minutes, 1))

    else:  # highlight
        d = item["digest"]
        dg = select.Digest(d["title"], tuple(select.DigestItem(sel_from(i["sel"]), i["label"]) for i in d["items"]),
                           d["description"], d["thumb_text"])
        segs: dict[int, object] = {}

        def scene_seg(s):
            if s.cand_id not in segs:
                segs[s.cand_id] = fetch_segment(vod_id, s.in_sec, s.out_sec, work / "segments",
                                                f"seg_h{s.cand_id:02d}", video.length_sec)
            return segs[s.cand_id]

        with ThreadPoolExecutor(pipeline.DOWNLOAD_WORKERS) as ex:
            list(ex.map(lambda i: scene_seg(i.sel), dg.items))
        f, th = pipeline.make_digest(dg, video, msgs, ng_words, peak_of, scene_seg, out, work, cfg,
                                     {c.id: c.top_messages for c in cands}, 1)
        res.update(file=f, title=publish_youtube.digest_title(dg.title, cfg.youtube),
                   description=publish_youtube.digest_description(video, dg, cfg.youtube), thumb=th,
                   minutes=round(dg.total_sec / 60, 1))

    res["youtube_id"], res["pending"] = None, False
    if youtube and cfg.youtube.enabled and item.get("youtube") and not live_now(cfg):
        token = pipeline.youtube_token()
        if token:
            up, pend = pipeline.upload_jobs(token, [(res["file"], res["title"], res["description"], res["thumb"],
                                                     res.get("game"))],
                                            out, cfg, 1)
            res["youtube_id"] = up.get(res["file"])
            res["pending"] = bool(pend)
    elif youtube and cfg.youtube.enabled:
        res["pending"] = True   # 1日の上限の外。翌日以降に上げ直す
    # ドライブは上げ直し待ちの置き場。YouTubeに上がった動画はドライブに置かない（サムネなどは置く）
    if res["youtube_id"] and res.get("file"):
        (out / res["file"]).unlink(missing_ok=True)
    publish_drive.upload(out, p["drive_folder"], cfg.drive)
    rdir = work / "results"
    rdir.mkdir(exist_ok=True)
    (rdir / f"{item_id}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(work / "segments", ignore_errors=True)
    return res


# ---------- 仕上げ ----------

def upload_md(video: VideoInfo, results: list[dict], privacy: str) -> str:
    """retry_pending が読める形（## ファイル名 → タイトル・概要欄のコードブロック）。"""
    lines = [f"# YouTube用テキスト：{video.title}", ""]
    for r in results:
        if not r.get("file"):
            continue
        yt = (f"YouTube：https://youtu.be/{r['youtube_id']}（{privacy}でアップロード済み）" if r.get("youtube_id")
              else "YouTube：未アップロード（上げ直し待ち）" if r.get("pending") else "YouTube：未アップロード")
        game = [f"ゲーム：{r['game']}", ""] if r.get("game") is not None else []
        lines += [f"## {r['file']}（{r['kind']}）", "", yt, "", *game, "タイトル：", "```", topics.fix_words(r["title"]), "```",
                  "概要欄：", "```", topics.fix_words(r["description"]), "```", ""]
    return "\n".join(lines)


def finalize(vod_id: str, cfg: Config, workdir: Path, state_mod=None) -> dict:
    from kirinuki import state as st_mod
    state_mod = state_mod or st_mod
    work = work_dir(workdir, vod_id)
    p = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    video = video_from(p["video"])
    results = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((work / "results").glob("*.json"))]
    order = {it["id"]: k for k, it in enumerate(p["items"])}
    results.sort(key=lambda r: order.get(r["id"], 99))
    new = results
    if p.get("mode") == "add":
        results = p["prev_results"] + new   # upload.md には前回の分も載せる（上げ直しがタイトルを読む）
    out = work / "out_final"
    out.mkdir(parents=True, exist_ok=True)
    (out / "upload.md").write_text(upload_md(video, results, cfg.youtube.privacy), encoding="utf-8")
    summary_lines = [f"# {video.title}（{vod_url(vod_id)}）", "", f"- 作成：{datetime.now(JST):%Y-%m-%d %H:%M}",
                     f"- 選び方：{p.get('method', '')}", ""]
    for r in results:
        summary_lines.append(f"- [{r['kind']}] {r.get('title', '')}　{r.get('minutes', '')}分　"
                             + (f"https://youtu.be/{r['youtube_id']}" if r.get("youtube_id") else "未アップロード"))
    (out / "candidates.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({**p, "results": results}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    publish_drive.upload(out, p["drive_folder"], cfg.drive)
    missing = [it["id"] for it in p["items"] if it["id"] not in {r["id"] for r in results}]
    st = state_mod.load()
    if p.get("mode") == "add":
        state_mod.record(st, vod_id, "done", datetime.now(JST), **merge_add(st.get(vod_id, {}), p["drop"], new),
                         failed_items=missing)
    else:
        state_mod.record(st, vod_id, "done" if results else "failed", datetime.now(JST), title=video.title,
                         drive_folder=p["drive_folder"],
                         youtube_ids=[r["youtube_id"] for r in results if r.get("youtube_id")],
                         youtube_pending=[r["file"] for r in results if r.get("pending")],
                         files=[r["file"] for r in results if r.get("file")], failed_items=missing)
    state_mod.save(st)
    used = sum(1 for r in new if r.get("youtube_id"))
    now = datetime.now(JST)
    state_mod.record_uploads([r["file"] for r in new if r.get("youtube_id")], now)
    retried = 0 if live_now(cfg) else pipeline.retry_pending(st, cfg, workdir, pipeline.budget_now(cfg))
    if retried:
        state_mod.save(st)
    log(f"仕上げ：{len(new)}本（YouTube {used}本、上げ直し待ち {sum(1 for r in new if r.get('pending'))}本"
        f"、失敗 {len(missing)}本）")
    return {"results": results, "missing": missing}
