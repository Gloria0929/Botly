"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Search } from "lucide-react";
import Avatar from "@/components/ui/Avatar";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import { clock } from "@/lib/fmt";
import type { SearchHit } from "@/lib/types";

const TYPE_LABEL: Record<string, string> = {
  text: "消息",
  plan: "计划",
  task: "任务",
  task_done: "完成",
  handoff: "交接",
  approval: "审批",
  question: "提问",
  tool_call: "工具调用",
  tool_result: "工具返回",
  system: "系统",
  limit: "限额",
};

/**
 * CHT-08：群聊主线程全文搜索。
 * 只搜主线程——聊天页展示的就是主线程；点结果跳到那条消息的上下文。
 */
export default function SearchModal({
  open,
  chatId,
  onClose,
  onJump,
}: {
  open: boolean;
  chatId: string | null;
  onClose: () => void;
  onJump: (messageId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  // 只认最后一次请求的结果，避免快速输入时旧结果覆盖新结果
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHits([]);
    setTotal(0);
    setHasMore(false);
    setError("");
    seq.current += 1;
  }, [open]);

  useEffect(() => {
    if (!open || !chatId) return;
    const term = query.trim();
    seq.current += 1;
    const current = seq.current;
    if (!term) {
      setHits([]);
      setTotal(0);
      setHasMore(false);
      setError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const r = await api.searchMessages(chatId, term, { limit: 30 });
        if (current !== seq.current) return;
        setHits(r.results);
        setTotal(r.total);
        setHasMore(r.has_more);
        setError("");
      } catch (e: any) {
        if (current !== seq.current) return;
        setHits([]);
        setTotal(0);
        setHasMore(false);
        setError(e.message);
      } finally {
        if (current === seq.current) setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [query, chatId, open]);

  const loadMore = async () => {
    if (!chatId || !hits.length || loadingMore) return;
    const term = query.trim();
    const cursor = hits[hits.length - 1].seq;
    const current = ++seq.current;
    setLoadingMore(true);
    try {
      const r = await api.searchMessages(chatId, term, { limit: 30, before: cursor });
      if (current !== seq.current) return;
      setHits((prev) => [...prev, ...r.results]);
      setHasMore(r.has_more);
    } catch (e: any) {
      if (current === seq.current) setError(e.message);
    } finally {
      if (current === seq.current) setLoadingMore(false);
    }
  };

  const term = query.trim();
  const highlight = (text: string) => {
    if (!term) return text;
    const lower = text.toLowerCase();
    const needle = term.toLowerCase();
    const parts: React.ReactNode[] = [];
    let from = 0;
    for (;;) {
      const at = lower.indexOf(needle, from);
      if (at < 0) {
        parts.push(text.slice(from));
        break;
      }
      if (at > from) parts.push(text.slice(from, at));
      parts.push(
        <mark key={at} className="rounded bg-accent/25 px-0.5 text-ink">
          {text.slice(at, at + needle.length)}
        </mark>
      );
      from = at + needle.length;
    }
    return parts;
  };

  const sender = (hit: SearchHit) =>
    hit.sender_type === "user" ? "你" : hit.sender_bot_name || "系统";

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-xl"
      title={
        <span className="flex items-center gap-1.5">
          <Search size={14} className="text-accent" /> 搜索消息
        </span>
      }
      subtitle="只搜当前群聊的主线程；子任务线程里的内容不在结果中。"
    >
      <div className="space-y-2.5">
        <div className="relative">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
          />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入关键词，例如：结论 / 文件名 / 某个 Bot 说的话"
            className="field py-2 pl-8 pr-16 text-[12.5px]"
          />
          <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[10.5px] text-faint">
            {loading ? <Loader2 size={12} className="animate-spin" /> : term ? `${total} 条` : ""}
          </span>
        </div>

        <div className="max-h-[52vh] space-y-1.5 overflow-y-auto">
          {hits.map((hit) => (
            <button
              key={hit.id}
              onClick={() => onJump(hit.id)}
              className="block w-full rounded-lg border border-line px-2.5 py-2 text-left transition-colors hover:border-accent/40 hover:bg-panel2/60"
            >
              <div className="flex items-center gap-1.5 text-[10.5px] text-faint">
                {hit.sender_type === "bot" ? (
                  <Avatar value={hit.sender_bot_avatar || "🤖"} size={16} alt={sender(hit)} />
                ) : null}
                <span className="text-muted">{sender(hit)}</span>
                <span>· {TYPE_LABEL[hit.type] || hit.type}</span>
                <span className="ml-auto shrink-0">{clock(hit.created_at)}</span>
              </div>
              <div className="mt-1 line-clamp-3 text-[12px] leading-relaxed text-muted">
                {highlight(hit.snippet)}
              </div>
            </button>
          ))}

          {loading && !hits.length && (
            <div className="flex justify-center py-8">
              <Loader2 size={15} className="animate-spin text-faint" />
            </div>
          )}
          {!loading && !!term && !hits.length && !error && (
            <div className="rounded-lg border border-dashed border-line px-3 py-8 text-center text-[11.5px] leading-relaxed text-faint">
              没有找到匹配的消息
              <div className="mt-1 text-[10.5px]">换个更短的关键词试试，或确认内容是否在子线程里</div>
            </div>
          )}
          {error && (
            <div className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2.5 text-[11.5px] text-danger">
              {error}
            </div>
          )}
          {!term && !loading && (
            <div className="rounded-lg border border-dashed border-line px-3 py-8 text-center text-[11.5px] leading-relaxed text-faint">
              输入关键词后自动搜索
            </div>
          )}
          {hasMore && (
            <button
              onClick={loadMore}
              disabled={loadingMore}
              className="btn-ghost w-full justify-center text-[11.5px]"
            >
              {loadingMore ? <Loader2 size={12} className="animate-spin" /> : null} 加载更多
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
