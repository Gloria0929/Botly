/** NTF-06 Web Push：Service Worker 注册、订阅与注销。 */

export type PushSupport =
  | "unsupported" // 浏览器没有 Service Worker / PushManager
  | "insecure" // 非 HTTPS（localhost 除外），浏览器不提供推送
  | "denied" // 通知权限被拒绝，需要用户到浏览器设置里恢复
  | "idle" // 可用但未订阅
  | "subscribed"; // 已订阅

export function isSecureEnough(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.isSecureContext ||
    location.hostname === "localhost" ||
    location.hostname === "127.0.0.1"
  );
}

function supported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** 当前订阅状态（不发网络请求，只看本地 SW 与已存在的订阅）。 */
export async function pushState(): Promise<PushSupport> {
  if (!supported()) return "unsupported";
  if (!isSecureEnough()) return "insecure";
  if (Notification.permission === "denied") return "denied";
  const reg = await navigator.serviceWorker.getRegistration();
  if (!reg) return "idle";
  const sub = await reg.pushManager.getSubscription();
  return sub ? "subscribed" : "idle";
}

function urlB64ToUint8Array(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** 注册 Service Worker（幂等）。 */
export async function registerSW(): Promise<ServiceWorkerRegistration | null> {
  if (!supported()) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js");
  } catch {
    return null;
  }
}

export interface SubscribeResult {
  endpoint: string;
}

/** 请求权限 → 注册 SW → 生成订阅 → 上报后端。 */
export async function subscribePush(): Promise<SubscribeResult> {
  if (!supported()) throw new Error("当前浏览器不支持 Web Push");
  if (!isSecureEnough()) throw new Error("Web Push 需要 HTTPS 环境");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("通知权限被拒绝，请到浏览器设置里开启");
  const reg = (await navigator.serviceWorker.register("/sw.js")) ?? (await navigator.serviceWorker.ready);
  const cfg = await api_pushConfig();
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlB64ToUint8Array(cfg.public_key) as BufferSource,
  });
  const json = sub.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    await sub.unsubscribe().catch(() => {});
    throw new Error("浏览器没有返回有效的订阅密钥");
  }
  try {
    await api_pushSubscribe({
      endpoint: json.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
      user_agent: navigator.userAgent.slice(0, 300),
    });
  } catch (e) {
    await sub.unsubscribe().catch(() => {});
    throw e;
  }
  return { endpoint: json.endpoint };
}

/** 注销：先后端删除，再让浏览器退订。 */
export async function unsubscribePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg ? await reg.pushManager.getSubscription() : null;
  if (sub) {
    await fetch("/api/push/unsubscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Botly-Client": "web" },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    }).catch(() => {});
    await sub.unsubscribe().catch(() => {});
  }
}

/** iOS / iPadOS 需要把站点添加到主屏幕才能收到推送。 */
export function isIOS(): boolean {
  if (typeof navigator === "undefined") return false;
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

// ---- 内部 fetch（避免与 lib/api 相互依赖成环） ----

async function api_pushConfig(): Promise<{ enabled: boolean; public_key: string }> {
  const res = await fetch("/api/push/config", {
    credentials: "same-origin",
    headers: { "X-Botly-Client": "web" },
  });
  if (!res.ok) throw new Error("无法获取推送配置");
  return res.json();
}

async function api_pushSubscribe(body: {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  user_agent?: string;
}): Promise<void> {
  const res = await fetch("/api/push/subscriptions", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Botly-Client": "web" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = "订阅上报失败";
    try {
      const d = await res.json();
      if (typeof d.detail === "string") msg = d.detail;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
}
