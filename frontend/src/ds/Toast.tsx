import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { animate, createSpring } from "animejs";

type ToastKind = "ok" | "warn" | "error" | "info";
interface ToastItem { id: number; text: string; kind: ToastKind }
type Push = (text: string, kind?: ToastKind) => void;

const ToastCtx = createContext<Push>(() => {});
export const useToast = () => useContext(ToastCtx);

/** Toast 反馈（anime.js spring 弹性出场）。页面用 useToast()("已保存","ok") */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const push = useCallback<Push>((text, kind = "info") => {
    const id = ++seq.current;
    setItems((a) => [...a, { id, text, kind }]);
    setTimeout(() => setItems((a) => a.filter((t) => t.id !== id)), 3200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="ds-toast-root">
        {items.map((t) => <ToastRow key={t.id} item={t} />)}
      </div>
    </ToastCtx.Provider>
  );
}

function ToastRow({ item }: { item: ToastItem }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const inst = animate(el, {
      opacity: [0, 1],
      translateY: [16, 0],
      scale: [0.96, 1],
      duration: 420,
      ease: createSpring({ stiffness: 220, damping: 18 }),
    });
    return () => { inst.pause(); };
  }, []);
  return <div ref={ref} className="ds-toast" data-kind={item.kind}>{item.text}</div>;
}
