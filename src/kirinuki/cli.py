"""コマンドライン：python -m kirinuki <サブコマンド>"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from kirinuki import chat as chatmod
from kirinuki import comments, peaks
from kirinuki.config import DEFAULT_CONFIG, load_config


def vod_id_arg(s: str) -> str:
    if not re.fullmatch(r"\d{5,15}", s):
        raise argparse.ArgumentTypeError("VOD ID は数字だけで指定する（例：2911434035）")
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kirinuki")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("peaks", help="チャットJSONから山場候補を出す（手元で試す用）")
    p.add_argument("--chat", type=Path, required=True)

    c = sub.add_parser("comments", help="指定区間の流れるコメントASSを作る（手元で試す用）")
    c.add_argument("--chat", type=Path, required=True)
    c.add_argument("--start", type=float, required=True)
    c.add_argument("--end", type=float, required=True)
    c.add_argument("-o", "--out", type=Path, required=True)

    r = sub.add_parser("run", help="VODを1本処理する（GitHub Actions上で使う）")
    r.add_argument("--vod-id", type=vod_id_arg, required=True)
    r.add_argument("--workdir", type=Path, default=Path("work"))
    r.add_argument("--no-upload", action="store_true")
    r.add_argument("--max-shorts", type=int)
    r.add_argument("--youtube", action="store_true", help="作ったショートをYouTubeに非公開でアップロードする")

    sub.add_parser("auto", help="新着VODを検知して処理する（定期実行用・第1版）")

    pl = sub.add_parser("plan", help="第2版：計画（VODを指定しなければ新着を自動で選ぶ）")
    pl.add_argument("--vod-id", type=vod_id_arg)
    pl.add_argument("--workdir", type=Path, default=Path("work"))
    pl.add_argument("--add", default="", help="足し算：作った動画はそのままで足す話題の番号（例 4,5）")
    pl.add_argument("--drop", default="", help="足し算：取りやめる上げ直し待ち（例 shorts/short_01.mp4）")
    pl.add_argument("--extra", default="", help="足し算：区間を手で決めた話題（開始-終了|タイトル|サムネの言葉 を ; 区切り）")
    ri = sub.add_parser("render-item", help="第2版：計画の動画1本を書き出す")
    ri.add_argument("--vod-id", type=vod_id_arg, required=True)
    ri.add_argument("--item", required=True)
    ri.add_argument("--workdir", type=Path, default=Path("work"))
    ri.add_argument("--youtube", action="store_true")
    fi = sub.add_parser("finalize", help="第2版：結果をまとめて記録する")
    fi.add_argument("--vod-id", type=vod_id_arg, required=True)
    fi.add_argument("--workdir", type=Path, default=Path("work"))

    ts = sub.add_parser("thumb-sample", help="計画の単体動画のサムネだけ作る（デザインの見本用）")
    ts.add_argument("--plan", type=Path, required=True)
    ts.add_argument("--out", type=Path, default=Path("samples"))

    pa = sub.add_parser("playlist-add", help="上げ済みの動画をゲームごとの再生リストに入れる（後から入れる用）")
    pa.add_argument("--pairs", required=True, help="動画ID=ゲーム名 をカンマ区切り")

    sub.add_parser("retry", help="新しい配信がないとき、上げ直し待ちの動画を空いた枠で上げる")

    a = sub.add_parser("auth", help="認証（Macで1回だけ実行）")
    a.add_argument("service", choices=["youtube"])

    args = ap.parse_args(argv)
    if args.cmd == "auth":
        from kirinuki import youtube_auth
        return youtube_auth.main()
    cfg = load_config(args.config)

    if args.cmd == "peaks":
        ch = chatmod.load_chat(args.chat)
        cands = peaks.detect(list(ch.messages), ch.video.length_sec, cfg.peaks)
        print(json.dumps([asdict(x) for x in cands], ensure_ascii=False, indent=2))
    elif args.cmd == "comments":
        ch = chatmod.load_chat(args.chat)
        cc = comments.clip_comments(
            list(ch.messages), args.start, args.end,
            comments.load_ng_words(cfg.ng_words_path()), cfg.comments, comments.short_layout(cfg.comments),
        )
        args.out.write_text(cc.ass, encoding="utf-8")
        print(f"区間内{cc.total} → フィルタ通過{cc.passed} → 表示{cc.shown}")
    elif args.cmd == "run":
        from kirinuki import pipeline, state
        summary = pipeline.run(args.vod_id, cfg, args.workdir, upload=not args.no_upload,
                               max_shorts=args.max_shorts, youtube=args.youtube)
        if args.youtube:  # 手動でYouTubeまで上げたものは、定期実行で二重に上げないよう記録する
            st = state.load()
            state.record(st, args.vod_id, "done", datetime.now(timezone.utc), manual=True, **summary)
            state.save(st)
    elif args.cmd == "auto":
        return run_auto(cfg)
    elif args.cmd == "plan":
        if args.add or args.drop or args.extra:
            return run_add(cfg, args.vod_id, args.workdir, args.add, args.drop, args.extra)
        return run_plan(cfg, args.vod_id, args.workdir)
    elif args.cmd == "render-item":
        from kirinuki import pipeline2
        pipeline2.render_item(args.vod_id, args.item, cfg, args.workdir, args.youtube)
    elif args.cmd == "finalize":
        from kirinuki import pipeline2
        pipeline2.finalize(args.vod_id, cfg, args.workdir)
    elif args.cmd == "retry":
        return run_retry(cfg)
    elif args.cmd == "playlist-add":
        from kirinuki import pipeline, publish_youtube
        token, cache = pipeline.youtube_token(), {}
        for pair in filter(None, (x.strip() for x in args.pairs.split(","))):
            vid, _, game = pair.partition("=")
            try:
                publish_youtube.add_to_playlist(token, vid.strip(), game.strip(), cache)
                print(f"[kirinuki] {vid.strip()} → 「{publish_youtube.playlist_title(game.strip())}」", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[kirinuki] {vid.strip()}: 失敗（{publish_youtube.http_error_text(e)[:300]}）", flush=True)
    elif args.cmd == "thumb-sample":
        return run_thumb_sample(args.plan, args.out)
    return 0


def run_thumb_sample(plan_path: Path, out: Path) -> int:
    from kirinuki import pipeline2, thumb2
    from kirinuki.fetch import fetch_segment
    p = json.loads(plan_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    # 見本の言葉を差し替える（「セリフ=大きくする語|…」を単体動画の順に。空なら計画のまま）
    texts = [x.partition("=") for x in os.environ.get("KIRINUKI_SAMPLE_TEXTS", "").split("|") if x.strip()]
    k = 0
    for it in p["items"]:
        if it["kind"] != "topic":
            continue
        v = pipeline2.topic_from(it["video"])
        text, emph = (texts[k][0].strip(), texts[k][2].strip()) if k < len(texts) else (v.thumb_text, v.thumb_emph)
        k += 1
        seg = fetch_segment(p["vod_id"], v.thumb_sec - 7, v.thumb_sec + 5, out / "seg", it["id"])
        ok = thumb2.render(seg, v.thumb_sec, text, v.game, v.face, v.bubbles, out / f"{it['id']}.jpg", emph)
        print(f"[kirinuki] {it['id']}: 「{text}」（大きく：{emph or 'なし'}）顔の切り抜き {'成功' if ok else 'なし'}", flush=True)
        seg.path.unlink(missing_ok=True)
    return 0


def run_retry(cfg) -> int:
    """上げ直し待ちを、直近24時間の枠が空いた分だけ上げる。配信中は上げない（CLAUDE.md ルール8）。"""
    from kirinuki import pipeline, state, twitch
    helix = twitch.Helix()
    if helix.is_live(helix.user_id(cfg.channel_login)):
        print("[kirinuki] 配信中なので上げ直さない", flush=True)
        return 0
    st = state.load()
    n = pipeline.retry_pending(st, cfg, Path("work"), pipeline.budget_now(cfg))
    state.save(st)
    print(f"[kirinuki] 上げ直し：{n}本", flush=True)
    return 0


def github_output(**kv) -> None:
    """GitHub Actions の次の段に値を渡す。"""
    import os
    path = os.environ.get("GITHUB_OUTPUT")
    text = "".join(f"{k}={v}\n" for k, v in kv.items())
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    print(text, end="", flush=True)


def run_plan(cfg, vod_id: str | None, workdir: Path) -> int:
    from datetime import timedelta

    from kirinuki import pipeline2, state, twitch
    more = 0
    if vod_id is None:
        helix = twitch.Helix()
        uid = helix.user_id(cfg.channel_login)
        live = helix.is_live(uid)
        todo = state.pending(helix.archives(uid), state.load(), datetime.now(timezone.utc), live,
                             timedelta(hours=cfg.auto.max_age_hours))
        if live or not todo:
            print("[kirinuki] " + ("配信中なので何もしない" if live else "新しいアーカイブなし"), flush=True)
            github_output(has_work="false", vod_id="", items="[]", more="0")
            return 0
        vod_id, more = todo[0].vod_id, len(todo) - 1
        print(f"[kirinuki] 自動処理：{vod_id}「{todo[0].title}」（ほかに未処理 {more}本）", flush=True)
    p = pipeline2.plan(vod_id, cfg, workdir)
    github_output(has_work="true", vod_id=vod_id, items=json.dumps([i["id"] for i in p["items"]]), more=str(more))
    return 0


def run_add(cfg, vod_id: str | None, workdir: Path, add: str, drop: str, extra: str = "") -> int:
    from kirinuki import pipeline2, state
    if vod_id is None:
        raise SystemExit("足し算には --vod-id が要る")
    ids = [int(x) for x in add.replace("、", ",").split(",") if x.strip()]
    drops = [x.strip() for x in drop.replace("、", ",").split(",") if x.strip()]
    p = pipeline2.add_plan(vod_id, cfg, workdir, ids, drops, state.load(), pipeline2.parse_extra(extra))
    github_output(has_work="true", vod_id=vod_id, items=json.dumps([i["id"] for i in p["items"]]), more="0")
    return 0


def run_auto(cfg) -> int:
    """配信中なら何もしない。終わって10分以上たった未処理のVODを古い順に処理する。"""
    import shutil
    from datetime import timedelta

    from kirinuki import pipeline, state, twitch

    helix = twitch.Helix()
    uid = helix.user_id(cfg.channel_login)
    live = helix.is_live(uid)
    now = datetime.now(timezone.utc)
    st = state.load()
    todo = state.pending(helix.archives(uid), st, now, live, timedelta(hours=cfg.auto.max_age_hours))
    if live:
        print("[kirinuki] 配信中なので何もしない", flush=True)
    elif not todo:
        print("[kirinuki] 新しいアーカイブなし", flush=True)
    used = 0
    for a in todo[: cfg.auto.max_vods_per_run]:
        print(f"[kirinuki] 自動処理：{a.vod_id}「{a.title}」", flush=True)
        try:
            summary = pipeline.run(a.vod_id, cfg, Path("work"), youtube=True)
            used += len(summary.get("youtube_ids", []))
            state.record(st, a.vod_id, "done", datetime.now(timezone.utc), **summary)
        except Exception as e:  # noqa: BLE001 — 1本の失敗で残りを止めない
            status = "skipped_auth" if state.is_auth_error(str(e)) else "failed"
            state.record(st, a.vod_id, status, datetime.now(timezone.utc), title=a.title, error=str(e)[:300])
            print(f"[kirinuki] {a.vod_id}: {status}（{e}）", flush=True)
        finally:
            state.save(st)
            shutil.rmtree(Path("work") / a.vod_id, ignore_errors=True)
    # 前に上限で上げられなかった動画を、残りの枠で上げ直す
    if pipeline.retry_pending(st, cfg, Path("work"), pipeline.budget_now(cfg)):
        state.save(st)
    return 0
