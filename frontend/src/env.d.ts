/// <reference types="vite/client" />

// Vue SFC 类型声明，让 TS 识别 .vue 文件
declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}

// 构建期注入的自定义环境变量（见 src/config/endpoint.ts）
// ⚠️ VITE_ 变量在 `vite build` 时内联进产物，换域名必须重新构建
interface ImportMetaEnv {
  /** 后端 API 根地址，如 `https://api.example.com`；留空 = 相对路径（同源部署） */
  readonly VITE_API_BASE_URL?: string;
  /** WebSocket origin，如 `wss://api.example.com`；留空则由 API 地址推导 */
  readonly VITE_WS_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
