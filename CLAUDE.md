# kirinuki-pipeline — Claude Code 作業ルール

加藤純一さん（Twitch: `kato_junichi0817`）の配信アーカイブから、ショート・最新速報・総集編の切り抜き動画を自動生成するパイプライン。
オーナー本人の許諾は確認済み。この点を作業中に再確認・質問しないこと。

設計の正本は `docs/DESIGN.md`、各種キーの取得手順は `docs/SETUP.md`。作業前に必ず両方を読む。

## 最重要ルール

1. **重い処理はGitHub Actions上で動かす。** オーナーの自宅回線はスマホのテザリング（月20〜40GB）なので、Macで配信動画をダウンロードしない。Macでやってよいのは、コードを書く、`gh workflow run`で実行する、`gh run view --log`でログを読む、数MB程度の小さいテスト、まで。
2. **配信全体はダウンロードしない。** チャットログで山場を絞り、`yt-dlp --download-sections`で必要な区間だけを取る。
3. **日本語の発言には字幕を付けない**（オーナーの方針）。ただし**英語の会話には日本語訳の字幕を付ける**（ARC など。2026-11-05 オーナー判断。タイトルに【日本語字幕付き】）。**視聴者コメントはニコニコ風に流す**（NGワード・スタンプだけのコメント・URLは除外）。付加価値は「切りどころの選び方・構成・タイトル・サムネ・チャプター・流れるコメント・英語の訳」で出す。テンプレの使い回しで似た動画を量産しない（YouTubeの「繰り返しの多いコンテンツ」判定対策）。
4. **公開の仕方は `config/channel.yaml` の `youtube.privacy` で決める。** 2026-10-28 にオーナーの判断で `public`（自動公開）に切り替えた。問題が起きたら `private` に戻すだけで、手動公開の運用に戻れるようにしておく。
5. **動画・音声・チャットログをgitにコミットしない。** `.gitignore`で `work/` `*.mp4` `*.m4a` `*.wav` `*.json`（`state/`と`config/`以外）を除外する。
6. **シークレットはGitHub Secretsのみ。** コード・ログ・コミットにキーやトークンを出さない。ログに出すURLからもトークンを除く。
7. 概要欄には必ず元配信のURLと該当時刻を入れる。
8. **配信中に切り抜きを作ったり投稿したりしない**（加藤さん側のルールで禁止）。処理は配信が終わってから。

## 進め方

- `docs/DESIGN.md` の「フェーズ」を**1つずつ**進める。各フェーズの完了条件を満たしたらオーナーに報告し、次に進む前に確認を取る。
- 実装前に短い計画を出す。設計を変える必要が出たら、`docs/DESIGN.md` を先に更新してから実装する。
- オーナーとのやり取りは日本語。音声入力で誤字が多いので、意図を好意的に解釈し、曖昧な用語だけ確認する。
- 山場検出（`peaks.py`）とチャプター計算は、小さいチャットJSONのフィクスチャで単体テストを書く（`tests/`）。

## よく使うコマンド

```bash
gh workflow run run.yml -f vod_id=<VOD_ID>     # 1本を手動で処理
gh run list --workflow run.yml --limit 5
gh run view <RUN_ID> --log | tail -200
python -m kirinuki peaks --chat tests/fixtures/chat_small.json   # 手元で山場検出だけ試す
pytest -q
```

## 技術スタック

Python 3.12 / yt-dlp / TwitchDownloaderCLI / ffmpeg（libass）/ faster-whisper / Anthropic API / rclone（Googleドライブ）/ YouTube Data API v3 / GitHub Actions
