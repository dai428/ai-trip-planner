/**
 * 主题（浅色 / 深色）切换。
 *
 * 【为什么用 document.documentElement.dataset.theme 而不是给 body 加 class】
 *    把主题写在 <html> 上，CSS 变量一处定义、全站生效；
 *    而且首屏渲染前就能读到正确主题，不会出现"白闪一下再变黑"。
 */

import { ref, watchEffect } from 'vue'

export type ThemeName = 'light' | 'dark'

const STORAGE_KEY = 'xingji-theme'

function initialTheme(): ThemeName {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  // 跟随系统偏好
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

const theme = ref<ThemeName>(initialTheme())

// 模块级副作用只注册一次（多个组件调 useTheme 不会重复绑定）
watchEffect(() => {
  document.documentElement.dataset.theme = theme.value
  localStorage.setItem(STORAGE_KEY, theme.value)
})

export function useTheme() {
  const toggle = () => {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }
  return { theme, toggle }
}
