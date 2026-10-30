import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 用 rgb(var(--x-rgb) / <alpha-value>) 才能让 bg-accent/15 这类透明度类生效
        canvas: "rgb(var(--canvas-rgb) / <alpha-value>)",
        panel: "rgb(var(--panel-rgb) / <alpha-value>)",
        panel2: "rgb(var(--panel-2-rgb) / <alpha-value>)",
        line: "rgb(var(--line-rgb) / <alpha-value>)",
        ink: "rgb(var(--ink-rgb) / <alpha-value>)",
        muted: "rgb(var(--muted-rgb) / <alpha-value>)",
        faint: "rgb(var(--faint-rgb) / <alpha-value>)",
        accent: "rgb(var(--accent-rgb) / <alpha-value>)",
        accentink: "rgb(var(--accent-ink-rgb) / <alpha-value>)",
        ok: "rgb(var(--ok-rgb) / <alpha-value>)",
        warn: "rgb(var(--warn-rgb) / <alpha-value>)",
        danger: "rgb(var(--danger-rgb) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ["SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,.18), 0 8px 24px -12px rgba(0,0,0,.35)",
      },
      keyframes: {
        rise: {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseSoft: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: ".45" },
        },
      },
      animation: {
        rise: "rise .22s ease-out both",
        pulseSoft: "pulseSoft 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
