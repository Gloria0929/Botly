"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, AtSign, Loader2, Paperclip, Square, X, Zap } from "lucide-react";
import type { Bot, ChatFile } from "@/lib/types";
import Avatar from "@/components/ui/Avatar";
import { toast } from "@/lib/ui";

export default function Composer({
  members,
  running,
  disabledReason,
  onSend,
  onInterject,
  onStop,
  seed,
  onUpload,
}: {
  members: Bot[];
  running: boolean;
  disabledReason?: string | null;
  onSend: (content: string) => void;
  onInterject: (content: string) => void;
  onStop: () => void;
  seed?: { text: string; k: number } | null;
  onUpload?: (file: File) => Promise<ChatFile>;
}) {
  const [text, setText] = useState("");
  const [mention, setMention] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [attachments, setAttachments] = useState<ChatFile[]>([]);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (seed?.text) {
      setText(seed.text);
      ta.current?.focus();
    }
  }, [seed?.k, seed?.text]);

  useEffect(() => {
    if (ta.current) {
      ta.current.style.height = "auto";
      ta.current.style.height = Math.min(ta.current.scrollHeight, 160) + "px";
    }
  }, [text]);

  const filtered = members.filter((m) => m.name.toLowerCase().includes(mentionQuery.toLowerCase()));

  const insertMention = (b: Bot) => {
    const idx = text.lastIndexOf("@");
    const next = (idx >= 0 ? text.slice(0, idx) : text) + `@${b.name} `;
    setText(next);
    setMention(false);
    setMentionQuery("");
    ta.current?.focus();
  };

  const send = () => {
    const references = attachments.length ? `已上传的文件（当前群聊沙箱）：\n${attachments.map((f) => `- ${f.path}`).join("\n")}` : "";
    const v = [text.trim(), references].filter(Boolean).join("\n\n");
    if (!v || disabledReason || uploading) return;
    setAttachments([]);
    if (running) {
      // RUN-13：执行中发送的是插话，不新建执行
      onInterject(v);
      setText("");
      return;
    }
    onSend(v);
    setText("");
  };

  return (
    <div className="border-t border-line bg-panel/80 px-4 py-3 backdrop-blur sm:px-6">
      <div className="mx-auto max-w-[1100px]">
        {disabledReason && (
          <div className="mb-2 rounded-lg border border-warn/30 bg-warn/[0.08] px-3 py-1.5 text-[11.5px] text-warn">
            {disabledReason}
            {disabledReason.includes("模型") && <a className="ml-2 underline" href="/settings">配置模型接入</a>}
          </div>
        )}

        <div className="relative">
          {attachments.length > 0 && <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((file) => <span key={file.id} className="flex max-w-full items-center gap-1 rounded-lg border border-line bg-panel2 px-2 py-1 text-[11px] text-muted">
              <Paperclip size={11} /><span className="truncate">{file.path}</span>
              <button title="从本条消息移除附件" onClick={() => setAttachments((p) => p.filter((f) => f.id !== file.id))}><X size={11} /></button>
            </span>)}
          </div>}
          <input ref={fileInput} type="file" multiple className="hidden" onChange={async (e) => {
            const files = Array.from(e.target.files || []);
            e.target.value = "";
            if (!onUpload) return;
            setUploading(true);
            try {
              for (const file of files) {
                try {
                  const uploaded = await onUpload(file);
                  setAttachments((p) => [...p, uploaded]);
                } catch (error: any) { toast(error.message, "error"); }
              }
            } finally { setUploading(false); }
          }} />
          {mention && filtered.length > 0 && (
            <div className="absolute bottom-full left-0 mb-1.5 w-56 overflow-hidden rounded-xl border border-line bg-panel shadow-card">
              <div className="border-b border-line px-2.5 py-1.5 text-[10.5px] text-faint">
                选择要 @ 的成员
              </div>
              {filtered.map((b, i) => (
                <button
                  key={b.id}
                  onClick={() => insertMention(b)}
                  className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left ${
                    i === Math.min(mentionIndex, filtered.length - 1) ? "bg-panel2" : "hover:bg-panel2"
                  }`}
                >
                  <Avatar value={b.avatar} size={16} />
                  <span className="text-[12.5px]">{b.name}</span>
                  <span className="ml-auto text-[10.5px] text-faint">{b.role}</span>
                </button>
              ))}
            </div>
          )}

          <div className="flex items-end gap-2 rounded-xl border border-line bg-panel2 px-2.5 py-2 focus-within:border-accent/60">
            <textarea
              ref={ta}
              rows={1}
              value={text}
              onChange={(e) => {
                const v = e.target.value;
                setText(v);
                const m = v.match(/@([^\s@]*)$/);
                setMention(!!m);
                setMentionQuery(m ? m[1] : "");
                setMentionIndex(0);
              }}
              onKeyDown={(e) => {
                // 中文输入法确认候选词时的 Enter 不能当成发送
                if (e.nativeEvent.isComposing) return;
                if (mention && filtered.length > 0) {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setMentionIndex((i) => (i + 1) % filtered.length);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setMentionIndex((i) => (i - 1 + filtered.length) % filtered.length);
                    return;
                  }
                  if (e.key === "Enter" || e.key === "Tab") {
                    e.preventDefault();
                    insertMention(filtered[Math.min(mentionIndex, filtered.length - 1)]);
                    return;
                  }
                }
                if (e.key === "Escape") {
                  setMention(false);
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={
                running
                  ? "执行中：可输入补充，总指挥会在下一步前读到…"
                  : "交代任务，Enter 发送，Shift+Enter 换行，@ 指定成员"
              }
              className="max-h-40 min-h-[24px] flex-1 resize-none bg-transparent text-[13.5px] leading-relaxed text-ink outline-none placeholder:text-faint"
            />
            <button
              onClick={() => fileInput.current?.click()}
              disabled={!onUpload || uploading}
              title="上传文件；文本类文件可供 Bot 直接读取"
              className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-faint hover:bg-panel hover:text-ink disabled:opacity-40"
            >
              {uploading ? <Loader2 size={15} className="animate-spin" /> : <Paperclip size={15} />}
            </button>
            <button
              onClick={() => {
                setText((t) => t + "@");
                setMentionQuery("");
                setMention(true);
                ta.current?.focus();
              }}
              className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-faint hover:bg-panel hover:text-ink"
              title="@ 成员"
            >
              <AtSign size={15} />
            </button>
            {running && (
              <button
                onClick={onStop}
                className="mb-0.5 flex h-7 items-center gap-1.5 rounded-lg bg-danger px-2.5 text-[12px] font-medium text-white hover:opacity-90"
                title="停止执行，已完成的步骤与产物会保留"
              >
                <Square size={11} fill="currentColor" /> 停止
              </button>
            )}
            <button
              onClick={send}
              disabled={(!text.trim() && !attachments.length) || !!disabledReason || uploading}
              className="mb-0.5 flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-accentink transition-opacity disabled:opacity-30"
              title={running ? "插话（下一步前送达）" : "发送"}
            >
              <ArrowUp size={15} />
            </button>
          </div>
        </div>

        <div className="mt-1.5 flex items-center gap-3 px-0.5 text-[10.5px] text-faint">
          {running ? (
            <>
              <span className="flex items-center gap-1">
                <Zap size={10} /> 执行中发送 = 插话，总指挥在下一步前读取
              </span>
              <span>停止会保留已完成步骤与产物</span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-1">
                <Zap size={10} /> 默认发给总指挥
              </span>
              <span>子任务串行执行，委派深度 1</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
