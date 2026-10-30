"use client";

import {
  BookOpen,
  Bot,
  Brain,
  Calculator,
  ChartColumn,
  Compass,
  Globe,
  Palette,
  PenLine,
  ScanEye,
  Search,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";

/** 头像图标 key → lucide SVG 图标。头像字段存 key，不再存 emoji。 */
export const AVATAR_ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  compass: Compass,
  search: Search,
  pen: PenLine,
  calculator: Calculator,
  wrench: Wrench,
  chart: ChartColumn,
  palette: Palette,
  brain: Brain,
  book: BookOpen,
  zap: Zap,
  globe: Globe,
  review: ScanEye,
};

/** 老数据里存的 emoji 头像，映射到同一个 SVG 图标，界面上不再出现 emoji。
 *  这里用 Unicode 码点转义写 key，源码里不留 emoji 字面量；这些 key 只做
 *  查找用，永远不会被渲染出来。 */
const LEGACY_EMOJI_TO_KEY: Record<string, string> = {
  "\u{1F916}": "bot",
  "\u{1F9ED}": "compass",
  "\u{1F50D}": "search",
  "\u{270D}\u{FE0F}": "pen",
  "\u{270D}": "pen",
  "\u{1F9EE}": "calculator",
  "\u{1F6E0}\u{FE0F}": "wrench",
  "\u{1F6E0}": "wrench",
  "\u{1F4CA}": "chart",
  "\u{1F3A8}": "palette",
  "\u{1F9E0}": "brain",
  "\u{1F4DA}": "book",
  "\u{26A1}\u{FE0F}": "zap",
  "\u{26A1}": "zap",
  "\u{1F310}": "globe",
  "\u{1F9D0}": "review",
};

/**
 * 解析头像值对应的 SVG 图标。
 * - 图标 key → 对应图标；老 emoji 值 → 映射后的图标；空值 → 默认 Bot 图标。
 * - 图片地址、厂商单字标识等返回 null，走 Avatar 原有的图片/文字渲染。
 */
export function resolveAvatarIcon(value: string | null | undefined): LucideIcon | null {
  const v = (value || "").trim();
  if (!v) return Bot;
  if (AVATAR_ICONS[v]) return AVATAR_ICONS[v];
  const key = LEGACY_EMOJI_TO_KEY[v];
  if (key) return AVATAR_ICONS[key];
  return null;
}

/** 头像选择器里的 SVG 图标选项（与老 emoji 选项一一对应）。 */
export const AVATAR_PICKER_OPTIONS: { key: string; label: string; Icon: LucideIcon }[] = [
  { key: "bot", label: "默认", Icon: Bot },
  { key: "compass", label: "总指挥", Icon: Compass },
  { key: "search", label: "研究员", Icon: Search },
  { key: "pen", label: "写手", Icon: PenLine },
  { key: "calculator", label: "计算", Icon: Calculator },
  { key: "wrench", label: "程序员", Icon: Wrench },
  { key: "chart", label: "数据", Icon: ChartColumn },
  { key: "palette", label: "创意", Icon: Palette },
  { key: "brain", label: "思考", Icon: Brain },
  { key: "book", label: "阅读", Icon: BookOpen },
  { key: "zap", label: "闪电", Icon: Zap },
  { key: "globe", label: "全球", Icon: Globe },
];
