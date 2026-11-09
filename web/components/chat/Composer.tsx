"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, AtSign, Loader2, Paperclip, Square, X, Zap } from "lucide-react";
import type { Bot, ChatFile } from "@/lib/types";
import Avatar from "@/components/ui/Avatar";
import { toastError } from "@/lib/api";

export default function Composer({
  members,
  running,
  disabledReason,
  onSend,
  onInterject,
  onStop,
  seed,
  onUpload,
  delegationDepth,
  chatMode = "task",
  replyTo,
  onCancelReply,
}: {
  members: Bot[];
  running: boolean;
  disabledReason?: string | null;
  onSend: (content: string) => void;
  onInterject: (content: string) => void;
  onStop: () => void;
  seed?: { text: string; k: number } | null;
  onUpload?: (file: File) => Promise<ChatFile>;
  delegationDepth?: number;
  chatMode?: "task" | "free";
  replyTo?: { name: string; content: string } | null;
  onCancelReply?: () => void;
}) {
  const [text, setText] = useState("");
  const [mention, setMention] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [attachments, setAttachments] = useState<ChatFile[]>([]);
  const [uploading, setUploading] = useState(false);
  useEffect(() => { if (replyTo) ta.current?.focus(); }, [replyTo?.name, replyTo?.content]);

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

  const everyone: Bot = { id: "everyone", name: "全员", role: "全体成员", avatar: "bot",
    system_prompt: "", provider_id: null, model: null, temperature: 0.4, tools: [], deleted: false, display_name: "全员" };
  const filtered = [...members, ...(chatMode === "free" ? [everyone] : [])]
    .filter((m) => m.name.toLowerCase().includes(mentionQuery.toLowerCase()));
  // 停用的 Bot 只展示、不可选：键盘上下和回车也只在可用成员里走
  const selectable = filtered.filter((m) => m.enabled !== false);
  const activeMention = selectable[Math.min(mentionIndex, selectable.length - 1)];

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
    if (running && chatMode !== "free") {
      // RUN-13：执行中发送的是引导，不新建执行
      onInterject(v);
      setText("");
      return;
    }
    onSend(v);
    setText("");
  };

  return (
    <div className="chat-composer px-4 pb-5 pt-3 sm:px-6">
      <div className="w-full">
        {replyTo && <div className="mb-2 flex items-center gap-2 rounded-lg border-l-2 border-accent bg-panel2 px-3 py-2 text-[11.5px]">
          <div className="min-w-0 flex-1"><span className="text-accent">回复 {replyTo.name}</span>
            <p className="truncate text-muted">{replyTo.content}</p></div>
          <button type="button" title="取消回复" aria-label="取消回复" onClick={onCancelReply}><X size={13} /></button>
        </div>}
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
                } catch (error: any) { toastError(error); }
              }
            } finally { setUploading(false); }
          }} />
          {mention && filtered.length > 0 && (
            <div className="absolute bottom-full left-0 mb-1.5 w-56 overflow-hidden rounded-lg border border-line bg-panel p-1 shadow-pop">
              <div className="border-b border-line/70 px-2 py-1.5 text-[10.5px] text-faint">
                选择要 @ 的成员
              </div>
              {filtered.map((b) => {
                // BOT-06：停用的 Bot 不参与新执行，@ 列表里置灰、不可选
                const off = b.enabled === false;
                const active = !off && activeMention?.id === b.id;
                return (
                  <button
                    key={b.id}
                    type="button"
                    disabled={off}
                    onClick={() => insertMention(b)}
                    title={off ? "该 Bot 已停用，先到 Bot 页启用后再 @" : undefined}
                    className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left ${
                      off
                        ? "cursor-not-allowed opacity-45"
                        : active
                          ? "bg-panel2"
                          : "hover:bg-panel2"
                    }`}
                  >
                    <Avatar value={b.avatar} size={16} />
                    <span className={`text-[12.5px] ${off ? "text-faint" : ""}`}>{b.name}</span>
                    <span className="ml-auto text-[10.5px] text-faint">
                      {off ? "已停用" : b.role}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <div className="chat-composer-input flex flex-col gap-3 border border-line px-4 py-3 transition-colors">
            <textarea
              ref={ta}
              aria-label="消息内容"
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
                    if (selectable.length) setMentionIndex((i) => (i + 1) % selectable.length);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    if (selectable.length) setMentionIndex((i) => (i - 1 + selectable.length) % selectable.length);
                    return;
                  }
                  if (e.key === "Enter" || e.key === "Tab") {
                    e.preventDefault();
                    if (activeMention) insertMention(activeMention);
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
                  ? chatMode === "free" ? "继续聊天，或 @成员接话…" : "执行中：可输入补充，总指挥会在下一步前读到…"
                  : chatMode === "free" ? "说点什么，或 @成员、@全员…" : "描述你的任务，或 @ 一位成员…"
              }
              className="max-h-40 min-h-[48px] w-full resize-none bg-transparent text-[13.5px] leading-relaxed text-ink outline-none placeholder:text-faint"
            />
            <div className="flex w-full items-center gap-1.5 border-t border-line/60 pt-2">
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
            <span className="ml-1 mr-auto hidden text-[10px] text-faint sm:block">Enter 发送 · Shift + Enter 换行</span>
            <span className="mr-auto sm:hidden" />
            {running && (
              // CHT-03：执行进行中时发送按钮变为「停止」；引导改由 Enter 发送
              <button
                onClick={onStop}
                className="mb-0.5 flex h-7 items-center gap-1.5 rounded-lg bg-danger px-2.5 text-[12px] font-medium text-white transition-opacity hover:opacity-90"
                title={chatMode === "free" ? "停止当前消息的回应" : "停止执行，已完成的步骤与产物会保留"}
              >
                <Square size={11} fill="currentColor" /> 停止
              </button>
            )}
            {(!running || chatMode === "free") && (
              <button
                onClick={send}
                disabled={(!text.trim() && !attachments.length) || !!disabledReason || uploading}
                className="mb-0.5 flex h-8 w-8 items-center justify-center rounded-full bg-accent text-accentink transition-opacity disabled:opacity-30"
                title="发送"
              >
                <ArrowUp size={15} />
              </button>
            )}
            </div>
          </div>
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5 text-[10.5px] text-faint">
          {running ? (
            <>
              <span className="flex items-center gap-1">
                <Zap size={10} /> {chatMode === "free" ? "可连续发送 · 顺着群友的话聊 · @ 可指定对话对象" : "执行中 Enter 发送 = 补充要求，下一轮读取并继续原任务"}
              </span>
              <span>点「停止」结束执行，已完成步骤与产物保留</span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-1">
                <Zap size={10} /> {chatMode === "free" ? "成员自己决定是否回应 · @ 定向唤醒" : "默认发给总指挥"}
              </span>
              <span>
                {delegationDepth === 1
                  ? "委派深度 1：只有总指挥能派任务"
                  : `委派深度 ${delegationDepth ?? 2}：子 Bot 可再向下派任务`}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
