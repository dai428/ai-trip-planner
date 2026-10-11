import { createRouter, createWebHistory } from 'vue-router'

// 路由懒加载：首屏只加载首页，结果页/分享页按需加载 —— 首屏更快
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
    { path: '/trip/:id', name: 'trip', component: () => import('./views/ResultView.vue') },
    { path: '/history', name: 'history', component: () => import('./views/HistoryView.vue') },
    { path: '/s/:token', name: 'share', component: () => import('./views/ShareView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

export default router
