import type { CSSProperties } from "react";

/** 进度条（Uiverse 模式）：确定性 value(0-100) 或 indeterminate 流条 */
export default function ProgressBar({ value, indeterminate = false, style, className = "" }: {
  value?: number; indeterminate?: boolean; style?: CSSProperties; className?: string;
}) {
  return (
    <div className={`ds-progress ${className}`} style={style} {...(indeterminate ? { "data-indeterminate": "" } : {})}>
      <div className="ds-progress-fill" style={{ width: indeterminate ? undefined : `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
    </div>
  );
}
