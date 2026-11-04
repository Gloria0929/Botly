import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

export function usePanelResize(initialWidth = 320) {
  const [panelWidth, setPanelWidth] = useState(initialWidth);
  const widthRef = useRef(initialWidth);
  const resizing = useRef(false);
  const start = useRef({ x: 0, width: initialWidth });

  useEffect(() => {
    widthRef.current = panelWidth;
  }, [panelWidth]);

  const maxWidth = useCallback(() => {
    if (typeof window === "undefined") return 720;
    return Math.max(280, Math.min(760, window.innerWidth - 232 - 264 - 420));
  }, []);
  const clamp = useCallback((width: number) => Math.min(maxWidth(), Math.max(260, Math.round(width))), [maxWidth]);

  useEffect(() => {
    const saved = Number(localStorage.getItem("botly-panel-width"));
    if (Number.isFinite(saved) && saved >= 260) {
      const width = clamp(saved);
      widthRef.current = width;
      setPanelWidth(width);
    }
    const onResize = () => setPanelWidth((width) => {
      const next = clamp(width);
      widthRef.current = next;
      return next;
    });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp]);

  const startPanelResize = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizing.current = true;
    start.current = { x: event.clientX, width: panelWidth };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };

  const movePanelResize = (event: PointerEvent<HTMLDivElement>) => {
    if (!resizing.current) return;
    const width = clamp(start.current.width + start.current.x - event.clientX);
    widthRef.current = width;
    setPanelWidth(width);
  };

  const endPanelResize = (event: PointerEvent<HTMLDivElement>) => {
    if (!resizing.current) return;
    resizing.current = false;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      /* Pointer capture may already be released. */
    }
    localStorage.setItem("botly-panel-width", String(widthRef.current));
  };

  const resetPanelWidth = () => {
    const width = clamp(initialWidth);
    widthRef.current = width;
    setPanelWidth(width);
    localStorage.setItem("botly-panel-width", String(width));
  };

  const onPanelKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    let width = panelWidth;
    if (event.key === "ArrowLeft") width += 16;
    else if (event.key === "ArrowRight") width -= 16;
    else if (event.key === "Home") width = initialWidth;
    else return;
    event.preventDefault();
    width = clamp(width);
    widthRef.current = width;
    setPanelWidth(width);
    localStorage.setItem("botly-panel-width", String(width));
  };

  return { panelWidth, startPanelResize, movePanelResize, endPanelResize, resetPanelWidth, onPanelKeyDown };
}
