import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// Vite 配置：开发服务器 + 后端 API 代理
// 阶段0：开发期通过 proxy 转发 /api 到后端，避免 CORS 问题
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // 开发服务器端口，与后端 CORS allow_origins 保持一致
    port: 5173,
    host: true,
    proxy: {
      // 所有 /api/v1 请求转发到 FastAPI 后端
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    // 构建产物输出到 dist/，Nginx 容器会挂载此目录
    outDir: "dist",
    sourcemap: false,
  },
});
