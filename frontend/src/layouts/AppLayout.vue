<script setup lang="ts">
import { ListChecks, Library, LogOut, Palette } from "lucide-vue-next";
import { useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

// 工作台布局：安静左侧导航 + 当前用户 + 图标登出，不做装饰性卡片
const auth = useAuthStore();
const router = useRouter();

function handleLogout() {
  auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="app-shell">
    <aside class="app-nav">
      <div class="app-nav__brand">文献综述工作台</div>
      <nav class="app-nav__links">
        <RouterLink class="app-nav__link" :to="{ name: 'tasks' }">
          <ListChecks :size="16" />
          <span>任务</span>
        </RouterLink>
        <RouterLink class="app-nav__link" :to="{ name: 'knowledge' }">
          <Library :size="16" />
          <span>知识库</span>
        </RouterLink>
        <RouterLink class="app-nav__link" :to="{ name: 'styles' }">
          <Palette :size="16" />
          <span>引用样式</span>
        </RouterLink>
      </nav>
      <div class="app-nav__footer">
        <span class="app-nav__user" :title="auth.user?.email">
          {{ auth.user?.username ?? "未登录" }}
        </span>
        <button
          class="icon-button"
          type="button"
          title="退出登录"
          aria-label="退出登录"
          @click="handleLogout"
        >
          <LogOut :size="16" />
        </button>
      </div>
    </aside>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 208px 1fr;
  min-height: 100vh;
}

.app-nav {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.25rem 0.75rem;
  background: var(--color-card);
  border-right: 1px solid var(--color-border);
}

.app-nav__brand {
  font-size: 0.95rem;
  font-weight: 600;
  padding: 0 0.5rem;
}

.app-nav__links {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.app-nav__link {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.625rem;
  border-radius: 8px;
  color: var(--color-text);
  text-decoration: none;
  font-size: 0.9rem;
}

.app-nav__link:hover {
  background: var(--color-bg);
}

.app-nav__link.router-link-active {
  background: var(--color-bg);
  font-weight: 600;
}

.app-nav__footer {
  margin-top: auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0 0.5rem;
}

.app-nav__user {
  font-size: 0.85rem;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-main {
  padding: 1.5rem 2rem;
  min-width: 0;
}

/* 移动端：左侧导航折成顶部横向导航，内容占满宽度（Plan 4 Exit Gate 移动视口要求） */
@media (max-width: 720px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .app-nav {
    flex-direction: row;
    align-items: center;
    flex-wrap: nowrap;
    gap: 0.5rem;
    padding: 0.55rem 0.75rem;
    border-right: none;
    border-bottom: 1px solid var(--color-border);
  }

  /* 窄屏只留导航与登出，避免文字被挤成竖排 */
  .app-nav__brand,
  .app-nav__user {
    display: none;
  }

  .app-nav__links {
    flex-direction: row;
    gap: 0.25rem;
    flex: 1 1 auto;
    overflow-x: auto;
  }

  .app-nav__link {
    flex: 0 0 auto;
    white-space: nowrap;
  }

  .app-nav__footer {
    margin-top: 0;
    padding: 0;
  }

  .app-main {
    padding: 1rem;
  }
}
</style>
