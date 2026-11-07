"""tests/fixtures/chat_small.json を作り直すためのスクリプト（乱数は固定）。

    python tests/fixtures/make_chat_small.py

25分の配信を想定：
- ふだんは数秒に1件の雑談
- 600秒前後：「草」の弾幕（一番大きい山）
- 630秒前後：小さい山（600秒の山と60秒以内なので統合される）
- 1200秒前後：「うおおお」「!?」の盛り上がり（2番目の山）
- エモートだけのコメント、エモート混じりのコメントも含む
"""

import json
import random
from pathlib import Path

rng = random.Random(0)
CHATTER = ["こんにちは", "それな", "はい", "なるほど", "ナイス", "次なにやるの", "かわいい", "えぐい", "眠い", "おつ"]


def comment(t, body, fragments=None):
    return {
        "content_offset_seconds": round(t, 2),
        "message": {"body": body, "fragments": fragments or [{"text": body, "emoticon": None}]},
    }


comments = []
t = 0.0
while t < 1500:
    t += rng.uniform(2.5, 5.5)
    comments.append(comment(t, rng.choice(CHATTER)))
for i in range(40):
    comments.append(comment(600 + rng.uniform(0, 10), rng.choice(["草", "草", "ｗｗｗ", "wwwww"])))
for i in range(10):
    comments.append(comment(630 + rng.uniform(0, 5), rng.choice(["草", "それはない"])))
for i in range(25):
    comments.append(comment(1200 + rng.uniform(0, 10), rng.choice(["うおおおお", "!?", "神", "ファーー"])))
comments.append(comment(605, "Kappa", [{"text": "Kappa", "emoticon": {"emoticon_id": "25"}}]))
comments.append(comment(606, "Kappa 草", [
    {"text": "Kappa", "emoticon": {"emoticon_id": "25"}}, {"text": " 草", "emoticon": None}]))
comments.sort(key=lambda c: c["content_offset_seconds"])

data = {
    "video": {"id": "1234567890", "title": "テスト配信", "created_at": "2026-10-25T12:00:00Z",
              "start": 0, "end": 1500, "length": 1500, "game": "テストゲーム"},
    "comments": comments,
}
out = Path(__file__).with_name("chat_small.json")
out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{out} ({len(comments)} comments)")
