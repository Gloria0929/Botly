"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Loader2, RotateCw, Terminal as TerminalIcon } from "lucide-react";
import type { Terminal as XTerminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import { api, errText } from "@/lib/api";

type Connection = "connecting" | "connected" | "closed" | "unavailable";

export default function SandboxTerminal({
  chatId,
  visible = true,
}: {
  chatId: string;
  visible?: boolean;
}) {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [path, setPath] = useState("");
  const [sandboxId, setSandboxId] = useState("");
  const [error, setError] = useState("");
  const [reconnect, setReconnect] = useState(0);
  const host = useRef<HTMLDivElement>(null);
  const runtime = useRef<{
    terminal: XTerminal;
    fit: FitAddon;
    socket: WebSocket;
  } | null>(null);
  const isVisible = useRef(visible);
  isVisible.current = visible;

  useEffect(() => {
    let active = true;
    let terminal: XTerminal | null = null;
    let socket: WebSocket | null = null;
    let observer: ResizeObserver | null = null;
    let inputTimer: ReturnType<typeof setTimeout> | null = null;
    let sizeTimer: ReturnType<typeof setTimeout> | null = null;
    let bufferedInput = "";
    let ready = false;
    const disposables: { dispose: () => void }[] = [];
    setConnection("connecting");
    setError("");
    setPath("");
    setSandboxId("");

    const open = async () => {
      const state = await api.terminal(chatId);
      if (!active) return;
      if (!state.available || state.provider !== "cube") {
        setConnection("unavailable");
        setError(state.reason || "交互式终端需要连接 Cube 后端");
        return;
      }
      // 模块随聊天页打包，仅在浏览器打开终端时执行，避免状态中的页面请求已失效的独立分块。
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import(/* webpackMode: "eager" */ "@xterm/xterm"),
        import(/* webpackMode: "eager" */ "@xterm/addon-fit"),
      ]);
      if (!active || !host.current) return;
      const fontFamily =
        getComputedStyle(host.current).getPropertyValue("--font-mono").trim() ||
        "monospace";
      terminal = new Terminal({
        cursorBlink: true,
        fontSize: 12,
        fontFamily: `${fontFamily}, monospace`,
        lineHeight: 1.2,
        scrollback: 5000,
        disableStdin: true,
        convertEol: false,
        theme: {
          background: "#151515",
          foreground: "#dedede",
          cursor: "#dedede",
          selectionBackground: "#454545",
        },
      });
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.open(host.current);
      const fitVisible = () => {
        if (
          active &&
          isVisible.current &&
          host.current?.clientWidth &&
          host.current?.clientHeight
        )
          fit.fit();
      };
      fitVisible();
      observer = new ResizeObserver(fitVisible);
      observer.observe(host.current);
      // 路径由远端 shell 的 OSC 7 输出提供，浏览器不拼接 provider:/path。
      disposables.push(
        terminal.parser.registerOscHandler(7, (value) => {
          try {
            setPath(decodeURIComponent(new URL(value).pathname));
          } catch {
            /* ignore malformed shell metadata */
          }
          return true;
        }),
      );
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(
        `${protocol}//${window.location.host}/api/chats/${encodeURIComponent(chatId)}/terminal/ws?cols=${terminal.cols}&rows=${terminal.rows}`,
      );
      socket.binaryType = "arraybuffer";
      runtime.current = { terminal, fit, socket };

      disposables.push(
        terminal.onData((data) => {
          if (!ready || socket?.readyState !== WebSocket.OPEN) return;
          bufferedInput += data;
          if (!inputTimer)
            inputTimer = setTimeout(() => {
              inputTimer = null;
              if (socket?.readyState === WebSocket.OPEN && bufferedInput) {
                for (
                  let offset = 0;
                  offset < bufferedInput.length;
                  offset += 16384
                ) {
                  socket.send(
                    JSON.stringify({
                      type: "input",
                      data: bufferedInput.slice(offset, offset + 16384),
                    }),
                  );
                }
              }
              bufferedInput = "";
            }, 12);
        }),
      );
      disposables.push(
        terminal.onResize((size) => {
          if (sizeTimer) clearTimeout(sizeTimer);
          sizeTimer = setTimeout(() => {
            if (ready && socket?.readyState === WebSocket.OPEN)
              socket.send(JSON.stringify({ type: "resize", ...size }));
          }, 60);
        }),
      );

      socket.onmessage = (event) => {
        if (!active || !terminal) return;
        if (event.data instanceof ArrayBuffer) {
          terminal.write(new Uint8Array(event.data));
          return;
        }
        try {
          const message = JSON.parse(event.data);
          if (message.type === "ready") {
            ready = true;
            terminal.options.disableStdin = false;
            setConnection("connected");
            setSandboxId(message.sandbox_id);
            setError("");
            fitVisible();
            socket?.send(
              JSON.stringify({
                type: "resize",
                cols: terminal.cols,
                rows: terminal.rows,
              }),
            );
            if (isVisible.current) terminal.focus();
          } else if (message.type === "error") {
            ready = false;
            terminal.options.disableStdin = true;
            setPath("");
            setSandboxId("");
            setError(message.message || "终端连接失败");
            setConnection("closed");
          } else if (message.type === "exit") {
            ready = false;
            terminal.options.disableStdin = true;
            terminal.writeln(
              `\r\n[远端 shell 已退出，退出码 ${message.code ?? "—"}]`,
            );
            setConnection("closed");
          }
        } catch {
          setError("终端返回了无法识别的数据");
        }
      };
      socket.onclose = () => {
        if (!active) return;
        ready = false;
        if (terminal) terminal.options.disableStdin = true;
        setConnection("closed");
      };
      socket.onerror = () => {
        if (active) setError("终端连接中断，请重新连接");
      };
    };
    open().catch((e) => {
      if (active) {
        setError(errText(e, "终端连接失败"));
        setConnection("closed");
      }
    });
    return () => {
      active = false;
      if (inputTimer) clearTimeout(inputTimer);
      if (sizeTimer) clearTimeout(sizeTimer);
      observer?.disconnect();
      disposables.forEach((item) => item.dispose());
      socket?.close();
      terminal?.dispose();
      runtime.current = null;
    };
  }, [chatId, reconnect]);

  useEffect(() => {
    if (visible) {
      const frame = requestAnimationFrame(() => {
        if (runtime.current && host.current?.clientWidth) {
          runtime.current.fit.fit();
          runtime.current.terminal.focus();
        }
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [visible]);

  return (
    <section
      aria-label="沙箱终端"
      className="flex min-h-0 flex-1 flex-col bg-[#151515] font-mono text-[#dedede]"
    >
      <div className="flex shrink-0 items-center gap-1 border-b border-[#303030] px-2 py-2 text-[11px]">
        <TerminalIcon size={13} className="mr-1 shrink-0 text-[#999]" />
        <span className="px-2 py-1 text-white">交互终端</span>
        <span className="ml-auto text-[10px] text-[#999]">
          {
            {
              connecting: "连接中",
              connected: "已连接",
              closed: "已断开",
              unavailable: "未接入",
            }[connection]
          }
        </span>
        <button
          className="rounded p-1 text-[#999] hover:bg-[#262626] hover:text-white"
          title="重新连接终端"
          aria-label="重新连接终端"
          onClick={() => setReconnect((value) => value + 1)}
        >
          <RotateCw size={12} />
        </button>
      </div>
      {path && (
        <p
          className="shrink-0 truncate  border-[#252525] text-[10px] text-[#999]"
          title={path}
        >
        </p>
      )}
      {connection === "connecting" && (
        <p className="flex shrink-0 items-center gap-2 px-3 py-2 text-[11px] text-[#999]">
          <Loader2 size={12} className="animate-spin" />
          正在连接远端 shell…
        </p>
      )}
      {error && (
        <p
          role="alert"
          className="shrink-0 break-words px-3 py-2 text-[11px] text-[#f0a1a1]"
        >
          {error}
          {connection === "closed" && (
            <button
              className="ml-2 text-white underline"
              onClick={() => setReconnect((value) => value + 1)}
            >
              重新连接
            </button>
          )}
          {connection === "unavailable" && (
            <Link
              className="ml-2 text-white underline"
              href="/settings#setting-沙箱接入"
            >
              设置连接
            </Link>
          )}
        </p>
      )}
      <div
        ref={host}
        className="min-h-0 flex-1 overflow-hidden px-2 py-2"
        aria-label="真实沙箱交互终端"
      />
      {sandboxId && (
        <p
          className="shrink-0 truncate border-t border-[#252525] px-3 py-1.5 text-[9px] text-[#777]"
          title={sandboxId}
        >
          sandbox {sandboxId}
        </p>
      )}
    </section>
  );
}
