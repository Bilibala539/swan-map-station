import { useEffect, useRef, type CSSProperties } from "react";
import { animate, stagger } from "animejs";

/** 逐字入场（MotionSites「Slate」SplitText 模式，anime.js 实现）：
 *  挂载时把文本拆成单字 span，y:40→0 + 淡入，per-char 60ms 错峰。 */
export default function SplitLine({ text, style, delay = 0 }: {
  text: string; style?: CSSProperties; delay?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    for (const ch of text) {
      const s = document.createElement("span");
      s.className = "ds-split-char";
      s.textContent = ch === " " ? " " : ch;
      el.appendChild(s);
    }
    const inst = animate(el.children, {
      opacity: [0, 1],
      translateY: [40, 0],
      duration: 800,
      delay: stagger(60, { start: delay }), // per-char 60ms 错峰
      ease: "outQuart", // ≈ gsap power3.out
    });
    return () => { inst.pause(); };
  }, [text, delay]);
  return <span ref={ref} style={style} />;
}
