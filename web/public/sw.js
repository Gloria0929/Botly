/* Botly Service Worker（NTF-06 Web Push）。
 *
 * 职责：
 * 1. 接收推送：后端 events.notify 发出的通知经推送服务到这里；
 * 2. 页面已打开时不再弹系统通知（页面自己会通过 SSE 弹），避免重复；
 * 3. 点击通知跳回对应群聊。
 */
/* eslint-disable no-restricted-globals */

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function targetUrl(data) {
  const q = new URLSearchParams();
  if (data && data.chat_id) q.set("chat", data.chat_id);
  if (data && data.target_type && data.target_id) {
    q.set("target", data.target_type);
    q.set("id", data.target_id);
  }
  const s = q.toString();
  return "/chat" + (s ? "?" + s : "");
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = { title: "Botly", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    (async () => {
      // 任一窗口开着就交给页面处理（页面收到 SSE 后自己弹），避免一通知弹两次
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      console.log("[sw] push received", payload.id, "windows:", clients.length);
      if (clients.length > 0) return;
      await self.registration.showNotification(payload.title || "Botly", {
        body: payload.body || "",
        tag: payload.id || "botly",
        icon: "/icon.svg",
        badge: "/icon.svg",
        data: payload,
      });
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = targetUrl(event.notification.data);
  event.waitUntil(
    (async () => {
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of clients) {
        if ("focus" in client) {
          try {
            if (client.navigate && client.url !== new URL(url, self.location.origin).href) {
              await client.navigate(url);
            }
          } catch (err) {
            /* 某些浏览器不允许 navigate，退化为只聚焦 */
          }
          await client.focus();
          return;
        }
      }
      await self.clients.openWindow(url);
    })()
  );
});
