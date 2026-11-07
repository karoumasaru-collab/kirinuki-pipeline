"""Twitch EventSub（stream.offline）の購読を登録する（DESIGN.md 3.1「即時検知」）。

GitHub Actions の eventsub.yml から実行する。同じ配信者・同じ通知先の古い購読は消してから作り直す。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from kirinuki.twitch import HELIX, Helix

TYPE = "stream.offline"


def _request(helix: Helix, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{HELIX}/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Client-Id": helix.client_id, "Authorization": f"Bearer {helix.token}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode()
        return json.loads(text) if text else {}


def subscribe(login: str, callback: str) -> None:
    helix = Helix()
    uid = helix.user_id(login)
    existing = _request(helix, "GET", f"eventsub/subscriptions?type={TYPE}").get("data", [])
    for s in existing:
        if s["condition"].get("broadcaster_user_id") == uid:
            _request(helix, "DELETE", f"eventsub/subscriptions?id={s['id']}")
            print(f"古い購読を削除：{s['status']}（{s['transport'].get('callback')}）", flush=True)
    res = _request(helix, "POST", "eventsub/subscriptions", {
        "type": TYPE, "version": "1",
        "condition": {"broadcaster_user_id": uid},
        "transport": {"method": "webhook", "callback": callback, "secret": os.environ["TWITCH_EVENTSUB_SECRET"]},
    })
    for s in res.get("data", []):
        print(f"購読を作成：{login}（{uid}）の {TYPE} → {callback}　状態：{s['status']}", flush=True)


def status(login: str) -> None:
    helix = Helix()
    uid = helix.user_id(login)
    for s in _request(helix, "GET", f"eventsub/subscriptions?type={TYPE}").get("data", []):
        if s["condition"].get("broadcaster_user_id") == uid:
            print(f"{TYPE}：{s['status']}（{s['transport'].get('callback')}、作成 {s['created_at']}）", flush=True)


def send_test(callback: str) -> None:
    """本物と同じ形の stream.offline 通知に署名して窓口へ送る（窓口→GitHub の動作確認用）。"""
    import hashlib
    import hmac
    import uuid
    from datetime import datetime, timezone

    body = json.dumps({
        "subscription": {"type": TYPE, "version": "1"},
        "event": {"broadcaster_user_login": "test", "broadcaster_user_id": "0"},
    }).encode()
    msg_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sig = hmac.new(os.environ["TWITCH_EVENTSUB_SECRET"].encode(), msg_id.encode() + ts.encode() + body,
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(callback, data=body, method="POST", headers={
        "Content-Type": "application/json",
        # Python既定のUser-AgentはCloudflareのボット対策で弾かれる（error 1010）ので名乗る
        "User-Agent": "kirinuki-eventsub-test/1.0",
        "Twitch-Eventsub-Message-Id": msg_id,
        "Twitch-Eventsub-Message-Timestamp": ts,
        "Twitch-Eventsub-Message-Type": "notification",
        "Twitch-Eventsub-Message-Signature": f"sha256={sig}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"テスト通知：窓口の応答 {r.status} {r.read().decode()[:100]}", flush=True)
    except urllib.error.HTTPError as e:
        print(f"テスト通知：窓口の応答 {e.code} {e.read().decode()[:200]}", flush=True)
        raise


def archives(login: str) -> None:
    """最近のアーカイブと配信中かどうか（確認用）。"""
    from datetime import timedelta, timezone
    helix = Helix()
    uid = helix.user_id(login)
    print(f"配信中：{'はい' if helix.is_live(uid) else 'いいえ'}", flush=True)
    jst = timezone(timedelta(hours=9))
    for a in helix.archives(uid, first=10):
        print(f"{a.vod_id}　開始 {a.created_at.astimezone(jst):%m/%d %H:%M}　終了 {a.ended_at.astimezone(jst):%m/%d %H:%M}"
              f"（{a.duration_sec / 3600:.1f}時間）　{a.title}", flush=True)
