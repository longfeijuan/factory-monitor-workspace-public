import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "寻证｜主管监控调查工作台",
  description: "基于脱敏摄像头能力数据生成最小回看路线与人工复核事件单。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
