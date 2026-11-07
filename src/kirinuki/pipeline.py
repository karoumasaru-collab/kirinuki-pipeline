"""1本のVODを処理する流れ。

チャット → 山場候補 → 候補ごとに音声＋静止画 → 文字起こし → Claudeで選定
→ 採用した場面だけ1080pで取得 → ショート（黒帯タイトル・拡大・流れるコメント）＋サムネ
→ 最新速報（横長・チャプター付き）＋サムネ → YouTube（非公開）・ドライブ
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from kirinuki import chat as chatmod
from kirinuki import comments, digest as digestmod, overlay, peaks, publish_drive, publish_youtube, render, select
from kirinuki.config import Config
from kirinuki.fetch import fetch_audio, fetch_frame, fetch_segment
from kirinuki.report import JST, ShortResult, build_candidates_md, jst_date
from kirinuki.transcribe import transcribe_all


def log(msg: str) -> None:
    print(f"[kirinuki] {msg}", flush=True)


def thumb_time(s: select.Selection, peak_sec: float | None, rng: tuple[float, float] | None = None) -> float:
    """サムネの瞬間：Claudeの thumb_sec → 山場 → 区間の中央 の順で、区間（既定はショートの区間）内のものを使う。"""
    lo, hi = rng or s.short_range()
    for t in (s.thumb_sec, peak_sec):
        if t is not None and lo <= t < hi:
            return t
    return (lo + hi) / 2


DOWNLOAD_WORKERS = 4  # ダウンロードは通信待ちが主なので同時に行う


def gather_materials(vod_id: str, cands: list[peaks.Candidate], length_sec: float, work: Path, cfg: Config):
    """候補ごとの音声（文字起こし）と静止画を同時に取得する。失敗した候補は材料なしで続ける。"""
    audio, frames, fetched = {}, {}, {}

    def one(c: peaks.Candidate) -> None:
        try:
            seg = fetch_audio(vod_id, max(0.0, c.peak_sec - cfg.peaks.context_lead_sec),
                              c.peak_sec + cfg.peaks.context_tail_sec, work / "audio", f"aud_{c.id:02d}", length_sec)
            audio[c.id] = seg
            fetched[c.id] = (seg.start_sec, seg.end_sec)
        except Exception as e:  # noqa: BLE001 — 1件の失敗で全体を止めない
            log(f"候補{c.id}: 音声の取得に失敗（{e}）")
            fetched[c.id] = (c.start_sec, c.end_sec)
        try:
            frames[c.id] = fetch_frame(vod_id, c.peak_sec, work / "frames", f"frm_{c.id:02d}")
        except Exception as e:  # noqa: BLE001
            log(f"候補{c.id}: 静止画の取得に失敗（{e}）")

    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as ex:
        list(ex.map(one, cands))
    log(f"音声 {len(audio)}件・静止画 {len(frames)}件を取得。文字起こし中（{cfg.whisper.model}）")
    # 文字起こしは時間がかかるので、盛り上がり上位だけにする
    top = {c.id for c in sorted(cands, key=lambda c: c.score, reverse=True)[: cfg.output.transcribe_top]}
    audio = {cid: seg for cid, seg in audio.items() if cid in top}
    transcripts = {}
    if audio:
        try:
            transcripts = transcribe_all(audio, cfg.whisper.model, cfg.whisper.compute_type)
        except Exception as e:  # noqa: BLE001 — 文字起こし無しでもClaudeはチャットと画面で判断できる
            log(f"文字起こしに失敗（{e}）。文字起こし無しで続ける")
    return transcripts, frames, fetched


def decide(video, cands, transcripts, frames, fetched, ng_words, cfg: Config, n: int):
    """Claudeで選定する。キーが無い・失敗したときはスコア順で代用する。"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY が無いので、スコア順で代用")
        return select.fallback_selection(cands, video, n), None, "チャットの盛り上がりスコア順（Claude未使用）", None
    template = select.PROMPT_PATH.read_text(encoding="utf-8")
    content = select.build_content(template, video, cands, fetched, transcripts, frames, ng_words)
    try:
        raw, usage = select.call_claude(cfg.model, content)
    except Exception as e:  # noqa: BLE001
        log(f"Claudeでの選定に失敗したので、スコア順で代用（{e}）")
        return select.fallback_selection(cands, video, n), None, "チャットの盛り上がりスコア順（Claudeの選定に失敗）", None
    log(f"Claude（{usage.model}）入力 {usage.input_tokens}トークン・出力 {usage.output_tokens}トークン")
    sels = select.normalize_all(raw, cands, fetched, cfg.output.short_max_sec)
    if not sels:
        log("Claudeの出力に有効な候補が無いので、スコア順で代用")
        return select.fallback_selection(cands, video, n), usage, "チャットの盛り上がりスコア順（Claudeの出力が空）", None
    return sels, usage, f"Claude（{usage.model}）が文字起こし・チャット・画面を見て選定", raw.get("digests")


def youtube_token() -> str | None:
    creds = publish_youtube.Credentials.from_env()
    if creds is None:
        log("YouTubeのシークレットが無いので、アップロードしない")
        return None
    try:
        return publish_youtube.access_token(creds)
    except Exception as e:  # noqa: BLE001
        log(f"YouTubeの認証に失敗（{publish_youtube.http_error_text(e)}）")
        return None


def upload_jobs(token: str, jobs: list[tuple], base: Path, cfg: Config,
                budget: int) -> tuple[dict[str, str], list[str]]:
    """(ファイル名, タイトル, 概要欄, サムネ[, ゲーム名]) を順にアップロードする。

    ゲーム名があれば、タグに足し、ゲームごとの再生リストに入れる（失敗しても止めない）。

    YouTubeの1日の上限に当たったらそこで止める。({ファイル名: 動画ID}, 上げられなかったファイル名) を返す。
    """
    uploaded: dict[str, str] = {}
    playlists: dict[str, str] = {}
    for k, job in enumerate(jobs):
        filename, title, desc, thumb = job[:4]
        game = job[4] if len(job) > 4 else None
        if k >= budget:
            break
        try:
            vid = publish_youtube.upload_video(token, base / filename, title, desc, cfg.youtube,
                                               (game,) if game else ())
        except Exception as e:  # noqa: BLE001
            err = publish_youtube.http_error_text(e)
            log(f"{filename}: アップロードに失敗（{err[:200]}）")
            if publish_youtube.is_limit_error(err):
                log("YouTubeの1日のアップロード上限に当たったので、残りは次回に上げ直す")
                break
            continue
        uploaded[filename] = vid
        log(f"{filename}: アップロード（{cfg.youtube.privacy}） https://youtu.be/{vid}")
        if thumb and (base / thumb).exists():
            try:
                publish_youtube.set_thumbnail(token, vid, base / thumb)
            except Exception as e:  # noqa: BLE001
                log(f"{filename}: サムネの設定に失敗（{publish_youtube.http_error_text(e)[:200]}）")
        if game is not None and cfg.youtube.playlists:
            try:
                publish_youtube.add_to_playlist(token, vid, game, playlists)
                log(f"{filename}: 再生リスト「{publish_youtube.playlist_title(game)}」に追加")
            except Exception as e:  # noqa: BLE001 — 許可（youtube）が無いときなど
                log(f"{filename}: 再生リストへの追加に失敗（{publish_youtube.http_error_text(e)[:200]}）")
    pending = [j[0] for j in jobs if j[0] not in uploaded]
    return uploaded, pending


def upload_to_youtube(video, results: list[ShortResult], out: Path, cfg: Config,
                      digest_uploads: list[tuple[str, str, str, str]] = ()) -> tuple[dict[str, str], list[str]]:
    """速報を先に、続けてショート（面白い順）を非公開でアップロードする。digest_uploads は [(ファイル名, タイトル, 概要欄, サムネ)]。"""
    jobs = list(digest_uploads) + [(r.filename, publish_youtube.short_title(r.title, cfg.youtube),
                                     publish_youtube.short_description(video, r.start_sec, cfg.youtube), r.thumb)
                                    for r in sorted(results, key=lambda r: -r.quality)]
    token = youtube_token()
    if token is None:
        return {}, [j[0] for j in jobs]
    return upload_jobs(token, jobs, out, cfg, cfg.youtube.daily_upload_limit)


def budget_now(cfg: Config) -> dict[str, int]:
    from kirinuki import state
    return state.upload_budget(cfg.youtube.daily_upload_limit, cfg.youtube.short_daily_limit, datetime.now(JST))


def retry_pending(st: dict, cfg: Config, workdir: Path, budget: dict[str, int]) -> int:
    """上限などで上げられなかった動画を、ドライブから取り寄せて上げ直す。上げた本数を返す（記録もここでする）。

    枠は長い動画（long）とショート（short）で別（budget_now）。

    上げる順は動画ごとに、題材の優先（prefer → ふつう → avoid）→ 面白さ → 新しい配信（DESIGN.md 10.2）。"""
    from kirinuki import topics
    # ドライブの保存期間を過ぎた上げ直し待ちは捨てる（もう取り寄せられない）
    now = datetime.now(JST)
    for vid, e in st.items():
        if e.get("youtube_pending") and e.get("processed_at"):
            age = now - datetime.fromisoformat(e["processed_at"])
            if age.total_seconds() > cfg.output.pending_max_hours * 3600:
                e["youtube_dropped"] = e.get("youtube_dropped", []) + e["youtube_pending"]
                e["youtube_pending"] = []
                log(f"{vid}: 上げ直し待ち {len(e['youtube_dropped'])}本は古くなったので取りやめ")
    todo = [(vid, e) for vid, e in st.items() if e.get("youtube_pending") and e.get("drive_folder")]
    if not todo or not any(n > 0 for n in budget.values()):
        return 0
    token = youtube_token()
    if token is None:
        return 0
    cands = []
    for vod_id, entry in todo:
        base = workdir / f"retry_{vod_id}"
        try:
            publish_drive.download(entry["drive_folder"], ["upload.md"], base, cfg.drive)
            md = (base / "upload.md").read_text(encoding="utf-8")
            texts, games = publish_youtube.parse_upload_md(md), publish_youtube.parse_upload_games(md)
        except Exception as e:  # noqa: BLE001
            log(f"{vod_id}: 上げ直しの準備に失敗（{e}）")
            continue
        quality: dict[str, float] = {}
        try:
            publish_drive.download(entry["drive_folder"], ["metadata.json"], base, cfg.drive)
            meta = json.loads((base / "metadata.json").read_text(encoding="utf-8"))
            quality = {r.get("file"): r.get("quality", 0) for r in meta.get("results", [])}
        except Exception:  # noqa: BLE001 — 古い記録には無いことがある。面白さ0として扱う
            pass
        for f in entry["youtube_pending"]:
            if f not in texts:
                log(f"{vod_id}: {f} のタイトルが upload.md に無い")
                continue
            title, desc = texts[f]
            v = SimpleNamespace(game=games.get(f), title=title, quality=quality.get(f, 0))
            cands.append((topics.rank(v, cfg.output.prefer, cfg.output.avoid), entry.get("processed_at", ""),
                          vod_id, (f, title, desc, f"thumbs/{Path(f).stem}.jpg", games.get(f))))
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    from kirinuki import state
    left, done = dict(budget), 0
    for _, _, vod_id, job in cands:
        kind = "short" if state.is_short(job[0]) else "long"
        if left.get(kind, 0) <= 0:
            continue
        entry, base = st[vod_id], workdir / f"retry_{vod_id}"
        try:
            publish_drive.download(entry["drive_folder"], [job[0]], base, cfg.drive)
        except Exception as e:  # noqa: BLE001
            log(f"{vod_id}: {job[0]} の取り寄せに失敗（{e}）")
            continue
        try:   # サムネは無いことがある（ショートは自前のサムネを置かない）。無ければ YouTube の自動のまま
            publish_drive.download(entry["drive_folder"], [job[3]], base, cfg.drive)
        except Exception as e:  # noqa: BLE001
            if kind == "long":
                log(f"{vod_id}: {job[0]} のサムネを取り寄せられない（{e}）。YouTube の自動のサムネになる")
        uploaded, _ = upload_jobs(token, [job], base, cfg, 1)
        if not uploaded:
            break  # 上限に当たった（ほかの失敗も、次の回にまとめてやり直す）
        done += 1
        left[kind] -= 1
        state.record_uploads(list(uploaded), datetime.now(JST))
        entry["youtube_ids"] = entry.get("youtube_ids", []) + list(uploaded.values())
        entry["youtube_pending"] = [f for f in entry["youtube_pending"] if f not in uploaded]
        publish_drive.delete(entry["drive_folder"], list(uploaded), cfg.drive)  # 上がった動画はドライブに置かない
        (base / job[0]).unlink(missing_ok=True)
    for vod_id, _ in todo:
        shutil.rmtree(workdir / f"retry_{vod_id}", ignore_errors=True)
    return done


def face_box(crop: select.Crop) -> tuple[float, float, float]:
    """サムネに大きく出す顔の位置：ワイプの指定 → カメラだけの指定 → よくある右下。"""
    if crop.mode == "both" and crop.fsize > 0:
        return (crop.fx, crop.fy, crop.fsize)
    if crop.mode == "face":
        return crop.main()
    return (0.88, 0.75, 0.35)


def thumb_bubbles(top: tuple, ng_words: tuple[str, ...]) -> tuple[str, ...]:
    """サムネの吹き出しに使うチャット（多かったもの上位から2つ。NG・長文・wだけは除く）。"""
    out = []
    for text, _ in top:
        t = text.strip()
        if not t or set(t.lower()) <= set("wｗ草 "):
            continue
        if any(w in comments.normalize_for_ng(t) for w in ng_words) or overlay.display_len(t) > 9:
            continue
        out.append(t)
        if len(out) == 2:
            break
    return tuple(out)


def make_digest(dg: select.Digest, video, msgs, ng_words, peak_of, scene_seg, out: Path, work: Path, cfg: Config,
                top_msgs: dict | None = None, num: int = 1):
    """最新速報とそのサムネを書き出す。(動画の相対パス, サムネの相対パス) を返す。"""
    top_msgs = top_msgs or {}
    layout = comments.digest_layout(cfg.comments)
    parts = []
    for k, item in enumerate(dg.items, start=1):
        s = item.sel
        seg = scene_seg(s)
        ass_path = None
        if cfg.comments.enabled:
            cc = comments.clip_comments(msgs, s.in_sec, s.out_sec, ng_words, cfg.comments, layout)
            ass_path = work / "segments" / f"digest{num}_{k:02d}.ass"
            ass_path.write_text(cc.ass, encoding="utf-8")
        part = work / f"digest_parts_{num}" / f"part_{k:02d}.mp4"
        digestmod.render_part(seg, s.in_sec, s.out_sec, part, ass_path)
        parts.append(part)
        log(f"速報{num} {k}/{len(dg.items)}：候補{s.cand_id}「{item.label}」{s.out_sec - s.in_sec:.0f}秒")
    mp4 = out / f"digest_{num}.mp4"
    digestmod.concat(parts, mp4)
    log(f"速報{num} 書き出し完了 {dg.total_sec / 60:.1f}分 {mp4.stat().st_size / 1e6:.1f}MB")
    thumb = ""
    try:
        best = max(dg.items, key=lambda i: i.sel.quality).sel
        at = thumb_time(best, peak_of.get(best.cand_id), (best.in_sec, best.out_sec))
        digestmod.render_thumbnail(scene_seg(best), at, dg.thumb_text, out / "thumbs" / f"digest_{num}.jpg",
                                   game=video.game_at(best.in_sec), face=face_box(best.crop),
                                   bubbles=thumb_bubbles(top_msgs.get(best.cand_id, ()), ng_words))
        thumb = f"thumbs/digest_{num}.jpg"
    except Exception as e:  # noqa: BLE001
        log(f"速報サムネの作成に失敗（{e}）")
    return f"digest_{num}.mp4", thumb


def make_short(s: select.Selection, name: str, seg, msgs, ng_words, cfg: Config, out: Path, work: Path,
               peak_sec: float | None) -> ShortResult:
    """ショート1本（縦・黒帯・拡大・流れるコメント）とサムネを書き出す。"""
    lo, hi = s.short_range()
    log(f"{name}: 候補{s.cand_id} {lo:.0f}〜{hi:.0f}秒「{s.title}」拡大={s.crop.mode}")
    crop, pip = s.crop.main(), s.crop.pip()
    layout = comments.short_layout(cfg.comments, pip=pip is not None)
    dur = hi - lo
    (work / "segments").mkdir(parents=True, exist_ok=True)
    band_styles, band_events = overlay.band_overlay(s.band, dur) if cfg.output.title_band else ([], [])
    ass_path = work / "segments" / f"{name}.ass"
    if cfg.comments.enabled:
        cc = comments.clip_comments(msgs, lo, hi, ng_words, cfg.comments, layout, band_styles, band_events)
        ass_text, shown, total = cc.ass, cc.shown, cc.total
        log(f"{name}: コメント 区間内{cc.total} → フィルタ通過{cc.passed} → 表示{cc.shown}")
    else:
        ass_text = overlay.ass_document(overlay.SHORT_W, overlay.SHORT_H, band_styles, band_events)
        shown = total = 0
    ass_path.write_text(ass_text, encoding="utf-8")
    mp4 = out / "shorts" / f"{name}.mp4"
    render.render_short(seg, lo, hi, mp4, ass_path, crop, pip)
    log(f"{name}: 書き出し完了 {mp4.stat().st_size / 1e6:.1f}MB")
    thumb_rel = ""
    try:
        t_styles, t_events = overlay.thumb_overlay(s.thumb_text)
        b_styles, b_events = overlay.band_overlay(s.band, 10)
        thumb_ass = work / "segments" / f"{name}_thumb.ass"
        thumb_ass.write_text(overlay.ass_document(
            overlay.SHORT_W, overlay.SHORT_H, b_styles + t_styles, b_events + t_events), encoding="utf-8")
        jpg = out / "thumbs" / f"{name}.jpg"
        render.render_thumbnail(seg, thumb_time(s, peak_sec), jpg, thumb_ass, crop, pip)
        thumb_rel = f"thumbs/{name}.jpg"
    except Exception as e:  # noqa: BLE001 — サムネの失敗で動画を捨てない
        log(f"{name}: サムネの作成に失敗（{e}）")
    return ShortResult(s.cand_id, f"shorts/{name}.mp4", lo, hi, shown, total,
                       title=s.title, thumb=thumb_rel, quality=s.quality)


def run(vod_id: str, cfg: Config, workdir: Path, upload: bool = True, max_shorts: int | None = None,
        youtube: bool = False) -> dict:
    """処理結果の要約（state に記録する内容）を返す。"""
    work = workdir / vod_id
    out = work / "out"
    if out.exists():
        shutil.rmtree(out)
    (out / "shorts").mkdir(parents=True)
    (out / "thumbs").mkdir()

    chat_path = work / "chat.json"
    if not chat_path.exists():
        log("チャットを取得中")
        chatmod.download_chat(vod_id, chat_path)
    chat = chatmod.load_chat(chat_path, vod_id)
    video = chat.video
    log(f"「{video.title}」 長さ {video.length_sec:.0f}秒 コメント {len(chat.messages)}件")

    msgs = list(chat.messages)
    cands = peaks.detect(msgs, video.length_sec, cfg.peaks)
    log(f"山場候補 {len(cands)}件")
    ng_words = comments.load_ng_words(cfg.ng_words_path())
    n = max_shorts if max_shorts is not None else cfg.output.shorts_per_vod

    transcripts, frames, fetched = gather_materials(vod_id, cands, video.length_sec, work, cfg)
    sels, usage, method, raw_digests = decide(video, cands, transcripts, frames, fetched, ng_words, cfg, n)
    o = cfg.output
    chosen = select.choose_shorts(sels, cands, n, min(o.shorts_min, n), o.short_min_quality)
    dgs = select.normalize_digests(raw_digests, sels, {s.cand_id for s in chosen}, o.digest_max_sec,
                                   o.digests_max, o.digest_max_short_scenes)
    log(f"採用 {sum(s.keep for s in sels)}件のうち、ショート {len(chosen)}本・速報 {len(dgs)}本"
        + "".join(f"（{len(d.items)}場面・{d.total_sec / 60:.1f}分）" for d in dgs) + "を作る")

    peak_of = {c.id: c.peak_sec for c in cands}
    scenes: dict[int, object] = {}

    def scene_seg(s: select.Selection):
        """場面全体（in〜out、余白付き）の1080p映像。ショートと速報で使い回す。"""
        if s.cand_id not in scenes:
            scenes[s.cand_id] = fetch_segment(vod_id, s.in_sec, s.out_sec, work / "segments",
                                              f"seg_{s.cand_id:02d}", length_sec=video.length_sec)
        return scenes[s.cand_id]

    # 使う場面の映像を、書き出しの前にまとめて同時に取得しておく
    needed = {s.cand_id: s for s in [*chosen, *(i.sel for d in dgs for i in d.items)]}
    t0 = datetime.now(JST)

    def prefetch(s: select.Selection) -> None:
        try:
            scene_seg(s)
        except Exception as e:  # noqa: BLE001 — 書き出しのときにもう一度試す
            log(f"候補{s.cand_id}: 映像の取得に失敗（{e}）")

    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as ex:
        list(ex.map(prefetch, needed.values()))
    log(f"場面の映像 {len(scenes)}/{len(needed)}件を取得（{(datetime.now(JST) - t0).seconds}秒）")

    results: list[ShortResult] = []
    for i, s in enumerate(chosen, start=1):
        results.append(make_short(s, f"short_{i:02d}", scene_seg(s), msgs, ng_words, cfg, out, work,
                                  peak_of.get(s.cand_id)))

    digest_uploads: list[tuple[str, str, str, str]] = []
    made: list[select.Digest] = []
    for num, dg in enumerate(dgs, start=1):
        try:
            d_file, d_thumb = make_digest(dg, video, msgs, ng_words, peak_of, scene_seg, out, work, cfg,
                                          {c.id: c.top_messages for c in cands}, num)
            digest_uploads.append((d_file, publish_youtube.digest_title(dg.title, cfg.youtube),
                                   publish_youtube.digest_description(video, dg, cfg.youtube), d_thumb))
            made.append(dg)
        except Exception as e:  # noqa: BLE001 — 速報の失敗でショートやほかの速報を捨てない
            log(f"速報{num}の作成に失敗（{e}）")

    uploaded: dict[str, str] = {}
    pending: list[str] = []
    if youtube and cfg.youtube.enabled:
        uploaded, pending = upload_to_youtube(video, results, out, cfg, digest_uploads)
    (out / "upload.md").write_text(
        publish_youtube.upload_md(video, results, cfg.youtube, uploaded, digest_uploads), encoding="utf-8")

    now = datetime.now(JST)
    sel_map = {s.cand_id: s for s in sels}
    md = build_candidates_md(video, cands, results, ng_words, now, sel_map, method)
    if digest_uploads:
        block = "## 最新速報\n\n" + "".join(
            f"### {u[0]}：{u[1]}（{d.total_sec / 60:.1f}分・{len(d.items)}場面）\n\n```\n{u[2]}\n```\n\n"
            for u, d in zip(digest_uploads, made))
        md = md.replace("## 候補（スコアの高い順）", block + "## 候補（スコアの高い順）", 1)
    (out / "candidates.md").write_text(md, encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({
        "video": asdict(video),
        "candidates": [asdict(c) for c in cands],
        "selections": [asdict(s) for s in sels],
        "shorts": [asdict(r) for r in results],
        "digests": [{"file": u[0], "title": d.title,
                     "items": [{"cand_id": i.sel.cand_id, "label": i.label, "in_sec": i.sel.in_sec,
                                "out_sec": i.sel.out_sec} for i in d.items]}
                    for u, d in zip(digest_uploads, made)],
        "usage": asdict(usage) if usage else None,
        "youtube": uploaded,
        "generated_at": now.isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    folder = f"{jst_date(video.created_at, now)}_{vod_id}"
    if upload:
        dest = publish_drive.upload(out, folder, cfg.drive)
        log(f"ドライブに保存：{dest}")
        publish_drive.cleanup(cfg.drive)
        log(f"{cfg.drive.retention_days}日より古いものを削除")
    return {
        "title": video.title,
        "shorts": [r.filename for r in results],
        "digests": [u[0] for u in digest_uploads],
        "youtube_ids": list(uploaded.values()),
        "youtube_pending": pending,
        "drive_folder": folder,
    }
