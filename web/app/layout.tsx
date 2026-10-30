import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/lib/ui";
import { ConfirmProvider } from "@/components/ui/Confirm";

export const metadata: Metadata = {
  title: "Botly · 多智能体工作台",
  description: "把多步骤工作交给一支 AI 团队，过程可见，风险可控。",
};

const themeScript = `
(function(){
  try{
    var t = localStorage.getItem('botly-theme');
    if(!t){ t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
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
      <body className="font-sans antialiased">
        <ConfirmProvider>{children}</ConfirmProvider>
        <Toaster />
      </body>
    </html>
  );
}
