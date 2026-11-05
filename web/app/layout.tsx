import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import "@xterm/xterm/css/xterm.css";
import { Toaster } from "@/lib/ui";
import { ConfirmProvider } from "@/components/ui/Confirm";
import GlobalError from "@/components/ui/GlobalError";

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Botly · 多智能体工作台",
  description: "把多步骤工作交给一支 AI 团队，过程可见，风险可控。",
  icons: { icon: "/icon.svg?v=collaboration" },
};

const themeScript = `
(function(){
  try{
    var t = localStorage.getItem('botly-theme');
    if(!t){ t = 'light'; }
    if(t === 'light') document.documentElement.classList.add('light');
  }catch(e){}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={`${sans.variable} ${mono.variable} font-sans antialiased`}>
        <ConfirmProvider>{children}</ConfirmProvider>
        <Toaster />
        <GlobalError />
      </body>
    </html>
  );
}
