import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "定了 · AI 决策闭环",
  description: "识别纠结、澄清偏好、作出决定、接受代价、停止反刍",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="mx-auto min-h-screen max-w-2xl px-4 py-8">{children}</div>
      </body>
    </html>
  );
}
