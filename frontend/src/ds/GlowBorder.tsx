import type { CSSProperties, ReactNode } from "react";

/** 旋转光边框（React Bits Glow Border 模式）：包一层 1px 旋转 conic 光圈。
 *  static=true 时停转（如打印/低动效偏好场景）。 */
export default function GlowBorder({ children, static: isStatic = false, style, className = "" }: {
  children: ReactNode; static?: boolean; style?: CSSProperties; className?: string;
}) {
  return (
    <div className={`ds-glow ${className}`} style={style} {...(isStatic ? { "data-static": "" } : {})}>
      <div className="ds-glow-inner">{children}</div>
    </div>
  );
}
