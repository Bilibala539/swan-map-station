import { useEffect, useRef } from "react";
import { animate } from "animejs";

/** 数字滚动（React Bits 模式，anime.js 重写）：挂载/值变化时从当前值滚到目标值 */
export default function CountUp({ to, duration = 900, suffix = "", className = "" }: {
  to: number; duration?: number; suffix?: string; className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const cur = useRef(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obj = { v: cur.current };
    const inst = animate(obj, {
      v: to,
      duration,
      ease: "outCubic",
      onUpdate: () => { el.textContent = `${Math.round(obj.v)}${suffix}`; },
      onComplete: () => { cur.current = to; },
    });
    return () => { inst.pause(); };
  }, [to, duration, suffix]);
  return <span ref={ref} className={`ds-countup ${className}`}>{cur.current}{suffix}</span>;
}
