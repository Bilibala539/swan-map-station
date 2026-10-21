import type { CSSProperties } from "react";

/** 分步指示器（两道门审核用）：steps=["机器初筛","人工复核"]，current=1 表示第二步进行中 */
export default function Stepper({ steps, current, style, className = "" }: {
  steps: string[]; current: number; style?: CSSProperties; className?: string;
}) {
  return (
    <div className={`ds-stepper ${className}`} style={style}>
      {steps.map((label, i) => {
        const state = i < current ? "done" : i === current ? "active" : "todo";
        return (
          <div className="ds-step" key={label} data-state={state}>
            {i > 0 && <span className="ds-step-link" data-done={i <= current ? "" : undefined} />}
            <span className="ds-step-dot">{i < current ? "✓" : i + 1}</span>
            <span>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
