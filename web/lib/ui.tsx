"use client";

import { useEffect, useState } from "react";

type Kind = "info" | "ok" | "error";
type Item = { id: number; text: string; kind: Kind };

let seq = 0;
const listeners = new Set<(i: Item) => void>();
// 1 秒内相同文案的去重：req 统一报错 + 调用方兜底报错时不会弹两条一样的
let lastText = "";
let lastKind = "";
let lastAt = 0;

export function toast(text: string, kind: Kind = "info") {
  const now = Date.now();
  if (text === lastText && kind === lastKind && now - lastAt < 1000) return;
  lastText = text;
  lastKind = kind;
  lastAt = now;
  const item = { id: ++seq, text, kind };
  listeners.forEach((l) => l(item));
}

export function Toaster() {
  const [items, setItems] = useState<Item[]>([]);

  useEffect(() => {
    const on = (i: Item) => {
      setItems((prev) => [...prev, i]);
      setTimeout(() => setItems((prev) => prev.filter((x) => x.id !== i.id)), 4200);
    };
    listeners.add(on);
    return () => {
      listeners.delete(on);
    };
  }, []);

  return (
    <div className="pointer-events-none fixed bottom-5 left-1/2 z-[100] flex -translate-x-1/2 flex-col items-center gap-2">
      {items.map((i) => (
        <div
          key={i.id}
          className={`max-w-[90vw] break-words animate-rise rounded-lg border px-3.5 py-2 text-[13px] shadow-card backdrop-blur ${
            i.kind === "error"
              ? "border-danger/40 bg-danger/15 text-danger"
              : i.kind === "ok"
                ? "border-ok/40 bg-ok/10 text-ok"
                : "border-line bg-panel/95 text-ink"
          }`}
        >
          {i.text}
        </div>
      ))}
    </div>
  );
}
