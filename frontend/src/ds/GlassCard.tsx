import type { CSSProperties, ReactNode } from "react";

/** 玻璃拟态卡片（React Bits Glass 模式，纯 CSS token 化） */
export default function GlassCard({ children, style, className = "" }: {
  children: ReactNode; style?: CSSProperties; className?: string;
}) {
  return <div className={`ds-glass ${className}`} style={style}>{children}</div>;
}
