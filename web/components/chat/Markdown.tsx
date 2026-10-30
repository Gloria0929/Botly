"use client";

import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 群聊里的 Markdown 渲染：支持 GFM 表格、任务列表、删除线、代码块和链接。
 * 原始 HTML 不渲染（react-markdown 默认行为），避免消息内容注入脚本。
 */
export default function Markdown({
  text,
  mention,
  size = "base",
  className = "",
}: {
  text: string;
  mention?: string;
  size?: "sm" | "base";
  className?: string;
}) {
  let source = text || "";
  if (mention) {
    const tag = `@${mention}`;
    if (source.includes(tag)) {
      // 把派活消息里的第一个 @成员 变成可点击样式的提及
      const label = tag.replace(/([\\[\]])/g, "\\$1");
      source = source.replace(tag, `[${label}](mention:${encodeURIComponent(mention)})`);
    }
  }

  const components: Components = {
    a({ href, children, ...props }) {
      if (href?.startsWith("mention:")) {
        return <span className="mention">{children}</span>;
      }
      return (
        <a href={href} target="_blank" rel="noreferrer noopener" {...props}>
          {children}
        </a>
      );
    },
    table({ children }) {
      return (
        <div className="prose-table">
          <table>{children}</table>
        </div>
      );
    },
  };

  return (
    <div
      className={`prose-msg ${size === "sm" ? "text-[12px]" : "text-[13.5px]"} leading-[1.72] text-ink ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        urlTransform={(url) => (url.startsWith("mention:") ? url : defaultUrlTransform(url))}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
