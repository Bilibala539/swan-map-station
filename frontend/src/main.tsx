import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initTheme } from "./theme";
import { ToastProvider } from "./ds";
import "./styles/tokens.css";
import "./styles/motion.css";

initTheme(); // 首帧前恢复主题记忆，避免闪色

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </React.StrictMode>,
);
