import { useState } from "react";
import type { CSSProperties } from "react";

/** 图片三态统一（治“截图损坏/空态丑”的根）：
 *  加载中=skeleton 微光；损坏=占位卡（图名+损坏说明）；空=占位卡（空态说明）。
 *  全站图片一律走这里，禁止裸 <img>。 */
export default function SmartImage({ src, alt, aspect = "9 / 16", className = "", style, emptyText = "该状态无截图" }: {
  src?: string | null; alt: string; aspect?: string; className?: string; style?: CSSProperties; emptyText?: string;
}) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  return (
    <div className={`ds-imgframe ${className}`} style={{ aspectRatio: aspect, ...style }}>
      {!src ? (
        <div className="ds-imgframe-empty">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.4" />
            <path d="M3 15l5-4 4 3 3-2 6 5" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
            <circle cx="9" cy="9.5" r="1.4" fill="currentColor" />
          </svg>
          <span>{emptyText}</span>
        </div>
      ) : state === "error" ? (
        <div className="ds-imgframe-empty ds-imgframe-error">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.4" />
            <path d="M8 12h8M12 8v8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <span>截图损坏或缺失</span>
          <span className="ds-imgframe-file">{src.split("/").pop()}</span>
        </div>
      ) : (
        <img
          className="ds-imgframe-img"
          src={src}
          alt={alt}
          loading="lazy"
          onLoad={() => setState("ok")}
          onError={() => setState("error")}
          style={{ opacity: state === "ok" ? 1 : 0 }}
        />
      )}
      {src && state === "loading" && <div className="ds-skeleton" style={{ position: "absolute", inset: 0 }} />}
    </div>
  );
}
