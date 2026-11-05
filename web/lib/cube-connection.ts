export const withHttpScheme = (raw: string) => {
  const value = raw.trim().replace(/\/+$/, "");
  return value && !value.includes("://") ? `http://${value}` : value;
};

export const formatCubeProxy = (ip: string, port = 80, protocol = "http:") => {
  if (!ip) return "";
  const host = ip.includes(":") && !ip.startsWith("[") ? `[${ip}]` : ip;
  return `${protocol}//${host}:${port}`;
};

export function parseCubeProxy(raw: string): { proxy_node_ip: string; proxy_port: number } {
  let proxy = raw.trim();
  if (!proxy) return { proxy_node_ip: "", proxy_port: 80 };
  if (!proxy.includes("://") && !proxy.startsWith("[") && proxy.split(":").length > 2) {
    proxy = `[${proxy}]`;
  }
  try {
    const authority = proxy.replace(/^https?:\/\//i, "").replace(/\/$/, "");
    if (/[/?#@\\\s]/.test(authority)) throw new Error();
    const address = new URL(withHttpScheme(proxy));
    if (!["http:", "https:"].includes(address.protocol) || address.username || address.password ||
      address.pathname !== "/" || address.search || address.hash ||
      (!address.hostname.startsWith("[") && !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(address.hostname))) {
      throw new Error();
    }
    // URL.port 会移除默认端口，单独读取显式的 :80 / :443。
    const explicitPort = proxy.match(/:(\d+)\/?$/)?.[1];
    const port = explicitPort ? Number(explicitPort) : 80;
    if (port < 1 || port > 65535 || /:\/?$/.test(proxy)) throw new Error();
    return { proxy_node_ip: address.hostname.replace(/^\[|\]$/g, ""), proxy_port: port };
  } catch {
    throw new Error("代理地址请填写有效的 http/https IP:端口，端口范围为 1–65535");
  }
}

export function normalizeCubeAddresses<T extends { api_url: string; proxy_address: string }>(form: T): T {
  let proxy = form.proxy_address.trim();
  try {
    const { proxy_node_ip, proxy_port } = parseCubeProxy(proxy);
    const protocol = proxy.match(/^(https?):\/\//i)?.[1].toLowerCase() || "http";
    proxy = formatCubeProxy(proxy_node_ip, proxy_port, `${protocol}:`);
  } catch {
    // 输入未完成时保留原文，在保存或测试时显示校验错误。
  }
  return { ...form, api_url: withHttpScheme(form.api_url), proxy_address: proxy };
}
