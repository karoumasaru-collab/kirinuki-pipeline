# セットアップ手順（フェーズ0）

Macのターミナルで、Claude Codeと一緒に上から順に進める。キーはすべて `gh secret set` で登録し、ファイルやチャットに貼らない。

## 1. GitHub

```bash
brew install gh rclone
gh auth login
cd ~/うんこちゃん切り抜き
git init && git add . && git commit -m "設計書を追加"
gh repo create kirinuki-pipeline --private --source . --push
```

GitHubの Settings → Billing で、Actionsの支払い上限（spending limit）を設定しておく。

## 2. Twitch（新着検知用）

1. https://dev.twitch.tv/console/apps で「アプリケーションを登録」
   - OAuthのリダイレクトURL：`http://localhost`
   - カテゴリ：Application Integration
   - クライアントタイプ：機密（Confidential）
2. クライアントIDとシークレットを登録：

```bash
gh secret set TWITCH_CLIENT_ID
gh secret set TWITCH_CLIENT_SECRET
```

## 3. Claude API

https://console.anthropic.com でAPIキーを発行し、使用上限を設定してから：

```bash
gh secret set ANTHROPIC_API_KEY
```

## 4. Googleドライブ（rclone）

rcloneの共用client_idは2026年中に廃止されるため、自分専用のclient_idを使う。

1. Google Cloud で `kirinuki` プロジェクトを作成（切り抜き用の無料アカウントで）→ Google Drive API を有効化。
2. GitHub Pages（公開リポジトリ `karoumasaru-collab.github.io`）にホームページとプライバシーポリシーを置く。
3. Google Auth Platform：
   - ブランディング：ホームページ `https://karoumasaru-collab.github.io/`、プライバシーポリシー `https://karoumasaru-collab.github.io/privacy.html`、承認済みドメイン `karoumasaru-collab.github.io`。ロゴは上げない（上げると審査が必要になる）。
   - 対象：外部 → **アプリを公開**（テストのままだとトークンが7日で失効）。
   - クライアント：デスクトップ アプリ。
4. `rclone config create gdrive drive scope=drive.file` の後、client_id/secret を設定してGitHubに登録する（値は画面・履歴・ファイルに残さない）：

```bash
bash -c 'read -r -p "client_id: " CID; read -r -s -p "client_secret: " CSEC; echo; rclone config update gdrive client_id="$CID" client_secret="$CSEC" config_refresh_token=true >/dev/null && rclone config show gdrive | gh secret set RCLONE_CONFIG --repo karoumasaru-collab/kirinuki-pipeline'
```

注意：シークレット入力は表示されない。1回だけ貼って Enter。トークンが表示された画面をスクリーンショットしない。

## 5. YouTube（フェーズ5で実施）

Google Cloudのプロジェクト・同意画面・公開ページ・OAuthクライアントは、4章で作ったもの（`kirinuki`）を使い回す。

1. 切り抜き用のYouTubeチャンネル名を「加藤純一 最速切り抜き」にし、概要欄に「非公式の切り抜きチャンネルです」を含む説明を入れる。
2. Google Cloud（`kirinuki` プロジェクト）で YouTube Data API v3 を有効化する。
3. Macで1回だけ次を実行し、ブラウザで**切り抜き用チャンネル**を選んで許可する。client_id/secret は4章と同じもの。値は画面・履歴・ファイルに残らず、3つのシークレット（YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN）としてGitHubに直接登録される。

```bash
cd ~/うんこちゃん切り抜き && .venv/bin/python -m kirinuki auth youtube
```

4. 最初は1本だけ上げて、YouTube Studioで「公開」に切り替えられるか確認する：

```bash
gh workflow run run.yml -f vod_id=<VOD_ID> -f max_shorts=1 -f youtube=true
```

## 6. 任意

- `TWITCH_AUTH_TOKEN`：サブスク限定アーカイブで取得に失敗したときだけ。
- `OPENAI_API_KEY`：文字起こしを外部APIに切り替える場合だけ。

## 確認

```bash
gh secret list
```

TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET / ANTHROPIC_API_KEY / RCLONE_CONFIG がそろえばフェーズ1に進める。
