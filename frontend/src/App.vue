<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import ThemeToggle from './components/ThemeToggle.vue'
</script>

<template>
  <header class="app-header">
    <div class="container row-between" style="height: 62px">
      <RouterLink to="/" class="brand">
        <span class="brand-mark" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12h4l2.5-7 3 14 2.5-7h6" />
          </svg>
        </span>
        <span>
          行迹
          <span class="hide-sm" style="color: var(--text-faint); font-weight: 400">· 智能行程规划</span>
        </span>
      </RouterLink>

      <nav class="row" style="gap: 4px">
        <RouterLink to="/" class="nav-link">规划</RouterLink>
        <RouterLink to="/history" class="nav-link">我的行程</RouterLink>
        <ThemeToggle />
      </nav>
    </div>
  </header>

  <main class="container" style="padding-top: 30px; padding-bottom: 72px">
    <RouterView v-slot="{ Component }">
      <component :is="Component" />
    </RouterView>
  </main>

  <footer class="container" style="padding-bottom: 34px">
    <div class="row-between wrap" style="gap: 8px; color: var(--text-faint); font-size: 12.5px">
      <span>行迹 · 智能行程规划 — 检索基于本地攻略库，预算与门票为估算值</span>
      <span>数据仅供规划参考，出行前请以官方信息为准</span>
    </div>
  </footer>
</template>
