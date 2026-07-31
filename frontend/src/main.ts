import { createApp } from "vue";
import App from "./App.vue";
import "./assets/main.css";

// 应用入口：阶段0仅挂载根组件
// 后续阶段按需引入 vue-router / pinia
createApp(App).mount("#app");
