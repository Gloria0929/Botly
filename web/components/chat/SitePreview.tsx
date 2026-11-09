"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Maximize2, Monitor, RotateCw, Square } from "lucide-react";
import { api, errText } from "@/lib/api";
import type { ChatFile, PreviewBackendStatus } from "@/lib/types";

export function SiteFrame({ file, revision = "" }: { file: ChatFile; revision?: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [backend, setBackend] = useState<PreviewBackendStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const cancelStartup = useRef<() => void>(() => {});

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let releaseWait: (() => void) | undefined;
    const cancel = () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
      releaseWait?.();
    };
    cancelStartup.current = cancel;
    setUrl(""); setError(""); setBackend(null); setStarting(false);
    api.fileSite(file.id).then(async (result) => {
      if (!active) return;
      if (result.backend) {
        setStarting(true);
        setBackend({ port: result.backend.port, running: false, ready: false, log: "", phase: "starting" });
        const startedAt = Date.now();
        let state = await api.fileBackend(file.id, "start", controller.signal);
        while (active) {
          setBackend(state);
          if (state.ready) break;
          if (!state.running || (state.phase && state.phase !== "starting")) {
            throw new Error(state.message || "启动进程已退出，请检查启动命令和日志。");
          }
          // 兼容尚未升级、只返回 running/ready 的 API，也避免浏览器无限等待。
          if (Date.now() - startedAt >= (state.timeout_seconds || 300) * 1000) {
            throw new Error(`启动进程仍在运行，但端口 ${state.port} 长时间未开始监听。请检查依赖安装、启动命令和日志。`);
          }
          await new Promise<void>((resolve) => { releaseWait = resolve; timer = setTimeout(resolve, 1500); });
          if (!active) return;
          state = await api.fileBackend(file.id, "status", controller.signal);
        }
      }
      if (active) setUrl(result.url);
    }).catch((e) => { if (active) setError(errText(e, "页面加载失败")); })
      .finally(() => { if (active) setStarting(false); });
    return cancel;
  }, [file.id, file.updated_at, revision, refresh]);

  return <div ref={container} className="flex min-h-0 flex-1 flex-col bg-panel">
    <div className="flex shrink-0 items-center justify-end gap-2 border-b border-line px-3 py-2">
      {backend && <button aria-label="停止预览后端" title="停止后端" disabled={stopping || (starting && !backend.running)} className="p-1 text-muted hover:text-ink disabled:opacity-50"
        onClick={async () => {
          cancelStartup.current();
          setStarting(false);
          setUrl("");
          setStopping(true);
          try { const state = await api.fileBackend(file.id, "stop"); setBackend(state); setError("后端已停止，点击刷新预览可重新启动。"); }
          catch (e) { setError(errText(e, "后端停止失败")); }
          finally { setStopping(false); }
        }}><Square size={12} /></button>}
      <button onClick={() => setRefresh((value) => value + 1)} title="刷新预览" aria-label="刷新预览" className="p-1 text-muted hover:text-ink"><RotateCw size={14} /></button>
      <button onClick={() => container.current?.requestFullscreen().catch(() => setError("当前浏览器无法全屏显示"))}
        title="全屏预览" aria-label="全屏预览" className="p-1 text-muted hover:text-ink"><Maximize2 size={14} /></button>
    </div>
    {backend && !backend.ready && <details open={!!error} className="shrink-0 border-b border-line px-3 py-2 text-[11px] text-muted">
      <summary className="cursor-pointer">启动日志</summary>
      {backend.log_path && <p className="mt-2 break-all font-mono text-[10px]">{backend.log_path}</p>}
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono">{backend.log || "启动进程暂未输出日志。"}</pre>
    </details>}
    {error ? <div role="alert" className="overflow-auto whitespace-pre-wrap break-words p-4 text-[12px] text-danger">{error}</div>
      : !url ? <div role="status" className="flex items-center gap-2 p-4 text-[12px] text-muted"><Loader2 size={15} className="shrink-0 animate-spin" />{starting ? "正在准备预览，请稍候…" : "正在加载页面…"}</div>
      : <iframe key={`${url}:${revision}:${refresh}`} title={`前端页面预览：${file.path}`} src={url}
          sandbox="allow-scripts" referrerPolicy="no-referrer" className="min-h-0 w-full flex-1 border-0 bg-white" />}
  </div>;
}

export default function SitePreview({ files }: { files: ChatFile[] }) {
  const pages = useMemo(() => files.filter((file) => /\.html?$/i.test(file.path)).sort((a, b) => {
    const score = (path: string) => (/\/(dist|build|out)\//.test(path) ? 2 : 0) + (/\/index\.html?$/i.test(path) ? 1 : 0);
    return score(b.path) - score(a.path) || (b.updated_at || "").localeCompare(a.updated_at || "") || a.path.localeCompare(b.path);
  }), [files]);
  const [selected, setSelected] = useState("");
  const page = pages.find((file) => file.id === selected) || pages[0];
  const directory = page?.path.slice(0, page.path.lastIndexOf("/") + 1);
  const revision = files.filter((file) => directory && file.path.startsWith(directory))
    .map((file) => `${file.id}:${file.updated_at}:${file.size}`).sort().join("|");

  if (!page) return <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
    <Monitor size={26} className="text-faint" />
    <p className="text-[13px] font-medium">还没有可预览的页面</p>
    <p className="max-w-xs text-[12px] leading-relaxed text-muted">Agent 构建完成后，output 中的前端页面会出现在这里。</p>
    <p className="text-[11px] text-faint">支持静态页面，以及配置了后端启动信息的前后端项目</p>
  </div>;

  return <section aria-label="前端预览" className="flex min-h-0 flex-1 flex-col">
    <div className="shrink-0 border-b border-line px-3 py-2">
      <label className="flex items-center gap-2 text-[12px] text-muted">页面
        <select aria-label="选择预览页面" value={page.id} onChange={(event) => setSelected(event.target.value)}
          className="min-w-0 flex-1 rounded-md border border-line bg-panel px-2 py-1.5 text-[12px] text-ink">
          {pages.map((file) => <option key={file.id} value={file.id}>{file.path}</option>)}
        </select>
      </label>
    </div>
    <SiteFrame key={page.id} file={page} revision={revision} />
  </section>;
}
