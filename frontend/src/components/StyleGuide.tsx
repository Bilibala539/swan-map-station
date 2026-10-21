import { useState } from "react";
import { CountUp, GlassCard, GlowBorder, SpotlightCard, ProgressBar, Stepper, useToast, useReveal } from "../ds";

/** 设计系统样式指南（0.3.0）：全部素材组件的活文档，兼无头自测靶子。深链 ?view=styleguide */
export default function StyleGuide() {
  const toast = useToast();
  const revealRef = useReveal<HTMLDivElement>();
  const [prog, setProg] = useState(64);
  const [gate, setGate] = useState(1); // 两道门：0=机器初筛 1=人工复核 2=完成

  return (
    <div className="content-inner">
      <h2 style={{ fontSize: "var(--fs-2xl)", marginTop: 0 }}>设计系统 · 素材组装指南 0.3.0</h2>
      <p style={{ color: "var(--muted)" }}>全部组件只消费 tokens.css 变量——新页面从这里拿组件，禁止绕过直写样式值（一致性硬判据）。</p>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, marginTop: 24 }}>
        <GlassCard style={{ padding: 20 }}>
          <h3 style={{ marginTop: 0 }}>CountUp 数字滚动</h3>
          <div style={{ fontSize: 34, fontWeight: 700 }}><CountUp to={87} suffix="%" /></div>
          <p style={{ color: "var(--muted)", fontSize: "var(--fs-sm)" }}>Dashboard 模块完成度 · React Bits 模式 / anime.js 重写</p>
        </GlassCard>

        <GlowBorder>
          <div style={{ padding: 20 }}>
            <h3 style={{ marginTop: 0 }}>GlowBorder 光边框</h3>
            <p style={{ color: "var(--muted)", fontSize: "var(--fs-sm)", marginBottom: 0 }}>当前功能区标注 · React Bits 模式 / conic 旋转</p>
          </div>
        </GlowBorder>

        <SpotlightCard style={{ padding: 20 }}>
          <h3 style={{ marginTop: 0 }}>Spotlight 聚光卡</h3>
          <p style={{ color: "var(--muted)", fontSize: "var(--fs-sm)", marginBottom: 0 }}>鼠标划过试试 · Aceternity 模式 / 纯 CSS</p>
        </SpotlightCard>
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>ProgressBar 进度条（Uiverse 模式）</h3>
        <ProgressBar value={prog} style={{ maxWidth: 420 }} />
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button className="btn" onClick={() => setProg((p) => Math.min(100, p + 8))}>+8</button>
          <button className="btn" onClick={() => setProg((p) => Math.max(0, p - 8))}>-8</button>
        </div>
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Stepper 分步指示（TestCaseReview 两道门）</h3>
        <Stepper steps={["机器初筛", "人工复核", "归档"]} current={gate} />
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button className="btn" onClick={() => setGate((g) => Math.max(0, g - 1))}>上一步</button>
          <button className="btn" onClick={() => setGate((g) => Math.min(2, g + 1))}>下一步</button>
        </div>
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Toast 反馈（anime.js spring）</h3>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => toast("审核通过，已归档", "ok")}>通过</button>
          <button className="btn" onClick={() => toast("用例存疑，退回重筛", "warn")}>退回</button>
          <button className="btn" onClick={() => toast("数据管线连接失败", "error")}>错误</button>
        </div>
      </section>

      <div ref={revealRef} className="ds-reveal card" style={{ marginTop: 16, marginBottom: 24 }}>
        <h3 style={{ marginTop: 0 }}>Scroll Reveal 入场（Aceternity 模式）</h3>
        <p style={{ color: "var(--muted)", fontSize: "var(--fs-sm)", marginBottom: 0 }}>本卡片进入视口时上浮淡入 · IntersectionObserver + CSS</p>
      </div>
    </div>
  );
}
