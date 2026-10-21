import { useEffect, useRef } from "react";

/** 滚动入场（Aceternity Reveal 模式）：目标进入视口时加 .ds-revealed。
 *  返回 ref 挂到任意元素；一次生效不回放（汇报场景防闪烁）。 */
export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") { el.classList.add("ds-revealed"); return; }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { el.classList.add("ds-revealed"); io.disconnect(); }
      }
    }, { threshold: 0.15 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return ref;
}
