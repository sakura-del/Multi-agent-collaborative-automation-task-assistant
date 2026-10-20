import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// Vite 配置：开发服务器 + 后端 API 代理 + 单元测试
export default defineConfig({
  // Tailwind v4 用 Vite 插件（不再需要 postcss.config / tailwind.config）
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "happy-dom",
    setupFiles: ["./src/test/setup.ts"],
    clearMocks: true,
    // 只跑单元测试；Playwright 规格（e2e/*.spec.ts）由 `npm run test:e2e` 负责
    include: ["src/**/*.test.ts"],
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
