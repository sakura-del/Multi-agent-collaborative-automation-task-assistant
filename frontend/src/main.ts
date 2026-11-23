import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import "./assets/main.css";
import { setUnauthorizedHandler } from "./api/client";
import { initTheme } from "./composables/useTheme";
import router from "./router";
import { useAuthStore } from "./stores/auth";

// 应用入口：初始化主题、Pinia + 路由；刷新失败时统一登出并回到登录页
initTheme();
const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
app.use(router);

setUnauthorizedHandler(() => {
  useAuthStore(pinia).logout();
  void router.push({ name: "login" });
});

app.mount("#app");
