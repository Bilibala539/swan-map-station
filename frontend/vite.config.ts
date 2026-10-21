import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  define: {
    // 构建时间戳：数据请求 URL 缓存击穿用（每次构建必变）
    __BUILD_TS__: JSON.stringify(Date.now()),
  },
  server: {
    // 开发期数据代理：/data、/assets 由后端服务器提供
    proxy: {
      "/data": "http://127.0.0.1:8090",
      "/assets": "http://127.0.0.1:8090",
      "/api": "http://127.0.0.1:8090",
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1200 },
});
