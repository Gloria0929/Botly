"use client";

import { useEffect, useState } from "react";
import { resolveAvatarIcon } from "./avatarIcons";

/** 统一的头像渲染：支持图片地址、图标 key（SVG）、以及带底色的文字标识（厂商标识）。
 *  老数据里的 emoji 头像也会映射到同一个 SVG 图标，不再直接显示 emoji；
 *  空值默认渲染 Bot 图标。 */
export default function Avatar({
  value,
  color,
  size = 32,
  rounded = "lg",
  className = "",
  alt = "",
}: {
  value?: string | null;
  color?: string;
  size?: number;
  rounded?: "lg" | "full";
  className?: string;
  alt?: string;
}) {
  const v = (value || "").trim();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [v]);

  const isImage =
    v.startsWith("/api/uploads/") || v.startsWith("http") || v.startsWith("data:");
  // LobeHub Icons 的静态 SVG：彩色版放在白色底片上，单色版跟随主题反色
  const isLobe =
    v.includes("@lobehub/icons-static-svg") || v.includes("icons.lobehub.com");
  const isMono = isLobe && v.endsWith(".svg") && !v.includes("-color.svg");
  const radius = rounded === "full" ? "rounded-full" : "rounded-lg";

  if (isImage && !failed && isLobe) {
    const inner = Math.round(size * 0.76);
    return (
      <span
        style={{ width: size, height: size }}
        className={`${radius} flex shrink-0 items-center justify-center overflow-hidden ${
          isMono ? "" : "border border-line bg-white"
        } ${className}`}
      >
        <img
          src={v}
          alt={alt}
          width={inner}
          height={inner}
          style={{ width: inner, height: inner }}
          onError={() => setFailed(true)}
          className={`object-contain ${isMono ? "lobe-mono" : ""}`}
        />
      </span>
    );
  }

  if (isImage && !failed) {
    return (
      <img
        src={v}
        alt={alt}
        width={size}
        height={size}
        style={{ width: size, height: size }}
        onError={() => setFailed(true)}
        className={`${radius} shrink-0 border border-line bg-panel2 object-cover ${className}`}
      />
    );
  }

  // 图标头像：新数据存图标 key，老数据里的 emoji 也映射到同一个 SVG；
  // 空值默认 Bot 图标。图片地址不走这条分支，图片加载失败时仍走下面的文字兜底。
  const Icon = resolveAvatarIcon(v);
  if (Icon && !failed) {
    const inner = Math.round(size * 0.58);
    return (
      <span
        style={{
          width: size,
          height: size,
          background: color || undefined,
        }}
        className={`${radius} flex shrink-0 items-center justify-center border ${
          color ? "border-transparent text-white" : "border-line bg-panel2 text-ink"
        } ${className}`}
        aria-label={alt || "头像"}
      >
        <Icon size={inner} strokeWidth={1.8} />
      </span>
    );
  }

  // 图片加载失败（例如 CDN 不可达）时退回文字标识，避免出现破图
  const raw =
    failed && (v.startsWith("http") || v.startsWith("/api/uploads/") || v.startsWith("data:"))
      ? (alt || "?").trim().slice(0, 1)
      : v;
  const text = raw || "?";
  const isLong = text.length > 2;
  return (
    <span
      style={{
        width: size,
        height: size,
        background: color || undefined,
        fontSize: isLong ? Math.max(9, size * 0.3) : Math.max(11, size * 0.46),
      }}
      className={`${radius} flex shrink-0 items-center justify-center border border-line font-medium leading-none ${
        color ? "border-transparent text-white" : "bg-panel2 text-ink"
      } ${className}`}
      aria-label={alt || text}
    >
      {text}
    </span>
  );
}
