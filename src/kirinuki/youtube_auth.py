"""YouTube のリフレッシュトークンを取得して GitHub Secrets に登録する（Macで1回だけ実行）。

    .venv/bin/python -m kirinuki auth youtube

- client_id / client_secret はターミナルで入力（シークレットは表示しない）
- ブラウザで切り抜き用チャンネルを選んで許可 → 手元の一時サーバーでコードを受け取る（PKCE）
- 3つの値を `gh secret set` に標準入力で渡す。画面・履歴・ファイルには残さない
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import http.server
import json
import secrets
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# youtube.upload：動画を上げる／youtube：再生リストを作って動画を入れる
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube"
REPO = "karoumasaru-collab/kirinuki-pipeline"


def _wait_for_code(port: int, state: str) -> str:
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                result["code"] = q["code"][0]
                msg = "許可を受け取りました。このタブは閉じて、ターミナルに戻ってください。"
            else:
                result["error"] = q.get("error", ["不明なエラー"])[0]
                msg = f"許可を受け取れませんでした：{result['error']}"
            body = f"<meta charset='utf-8'><p style='font-size:20px'>{msg}</p>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # アクセスログ（コードを含むURL）を出さない
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.handle_request)
    t.start()
    t.join(timeout=600)
    server.server_close()
    if "code" not in result:
        raise SystemExit(f"許可を受け取れませんでした（{result.get('error', 'タイムアウト')}）")
    return result["code"]


def main() -> int:
    print("YouTube へのアップロード許可を取得します。値は画面に表示されません。")
    client_id = input("client_id: ").strip()
    client_secret = getpass.getpass("client_secret（1回だけ貼って Enter・表示されません）: ").strip()
    if not client_id.endswith(".apps.googleusercontent.com") or not client_secret.startswith("GOCSPX-"):
        raise SystemExit("client_id / client_secret の形式が違います。貼り直してください。")

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    port = 53683
    redirect = f"http://127.0.0.1:{port}/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("ブラウザを開きます。**切り抜き用のYouTubeチャンネル**を選んで「許可」してください。")
    webbrowser.open(url)
    code = _wait_for_code(port, state)

    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=60) as r:
        refresh = json.load(r).get("refresh_token")
    if not refresh:
        raise SystemExit("リフレッシュトークンを受け取れませんでした。もう一度実行してください。")

    for name, value in (("YOUTUBE_CLIENT_ID", client_id), ("YOUTUBE_CLIENT_SECRET", client_secret),
                        ("YOUTUBE_REFRESH_TOKEN", refresh)):
        subprocess.run(["gh", "secret", "set", name, "--repo", REPO], input=value.encode(), check=True)
    print("ALL OK：GitHubに3つのシークレットを登録しました。")
    return 0
