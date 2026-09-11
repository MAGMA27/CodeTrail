import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import "@xyflow/react/dist/style.css";

export const metadata: Metadata = {
  title: "CodeTrail",
  description: "从 Issue 出发，看清代码关联与影响。",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
