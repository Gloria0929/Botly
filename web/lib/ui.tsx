"use client";

import { useEffect, useState } from "react";

type Kind = "info" | "ok" | "error";
type Item = { id: number; text: string; kind: Kind };

let seq = 0;
const listeners = new Set<(i: Item) => void>();

export function toast(text: string, kind: Kind = "info") {
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
          className={`animate-rise rounded-lg border px-3.5 py-2 text-[13px] shadow-card backdrop-blur ${
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
