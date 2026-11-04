/** 自动加载仅限已鉴权的图片/文件接口，拒绝路径穿越及认证重定向。 */
export function isSafeMarkdownImage(src: unknown): src is string {
  return typeof src === "string" && (
    /^\/api\/uploads\/[a-f0-9]{32}$/.test(src)
    || /^\/api\/files\/[a-f0-9]{32}\/(preview|download)$/.test(src)
  );
}
