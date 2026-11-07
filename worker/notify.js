// 加藤さんの配信終了（Twitch EventSub stream.offline）を受けて、GitHub の処理を起動する窓口。
// Cloudflare Workers に置く。DESIGN.md 3.1「即時検知」。
//
// 必要な設定（Cloudflare の Variables and Secrets）：
//   GH_REPO                 karoumasaru-collab/kirinuki-pipeline（テキスト）
//   TWITCH_EVENTSUB_SECRET  EventSub の署名検証用（シークレット。GitHub Secrets にも同じ値）
//   GH_TOKEN                このリポジトリだけ・Contents 書き込みの fine-grained トークン（シークレット）
//
// Cron Triggers：「47 * * * *」（毎時47分）。GitHub の定期実行は混むと飛ばされるので、
// 上げ直しの確認（retry.yml）はここから毎時合図を送って起動する。

export default {
  // 失敗したら例外にして、Cloudflare の Cron Events とログに理由を残す
  async scheduled(event, env, ctx) {
    const r = await dispatch(env, "retry_tick", {});
    console.log(`retry_tick → GitHub ${r.status}`);
    if (!r.ok) throw new Error(`github error ${r.status}: ${(await r.text()).slice(0, 300)}`);
  },

  async fetch(request, env) {
    if (request.method !== "POST") return new Response("kirinuki-notify");

    const body = await request.text();
    const h = (k) => request.headers.get(k) || "";
    const id = h("Twitch-Eventsub-Message-Id");
    const ts = h("Twitch-Eventsub-Message-Timestamp");
    const type = h("Twitch-Eventsub-Message-Type");

    // 本物の Twitch からの通知か、署名で確かめる
    if (!(await verify(env.TWITCH_EVENTSUB_SECRET, id + ts + body, h("Twitch-Eventsub-Message-Signature")))) {
      return new Response("bad signature", { status: 403 });
    }
    // 10分より古い通知は使い回しの可能性があるので受けない
    if (Math.abs(Date.now() - Date.parse(ts)) > 10 * 60 * 1000) {
      return new Response("stale", { status: 400 });
    }

    const msg = JSON.parse(body);
    if (type === "webhook_callback_verification") {
      return new Response(msg.challenge, { headers: { "Content-Type": "text/plain" } });
    }
    if (type === "notification" && msg.subscription?.type === "stream.offline") {
      const r = await dispatch(env, "stream_offline", { broadcaster: msg.event?.broadcaster_user_login || "" });
      if (!r.ok) return new Response(`github error ${r.status}`, { status: 502 });
    }
    return new Response("ok");
  },
};

// GitHub に repository_dispatch を送る（stream_offline → run.yml、retry_tick → retry.yml）
function dispatch(env, eventType, payload) {
  return fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kirinuki-notify",
    },
    body: JSON.stringify({ event_type: eventType, client_payload: payload }),
  });
}

async function verify(secret, message, signature) {
  if (!secret || !signature.startsWith("sha256=")) return false;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
  const hex = [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
  return safeEqual(hex, signature.slice(7));
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
