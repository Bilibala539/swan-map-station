import type { CSSProperties, ReactNode, MouseEvent } from "react";

/** 聚光卡（Aceternity Spotlight 模式）：鼠标位置写入 --mx/--my，radial 高光跟随 */
export default function SpotlightCard({ children, style, className = "", onClick }: {
  children: ReactNode; style?: CSSProperties; className?: string; onClick?: () => void;
}) {
  const onMove = (e: MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return <div className={`ds-spotlight ${className}`} style={style} onMouseMove={onMove} onClick={onClick}>{children}</div>;
}
