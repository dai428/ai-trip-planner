import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5273,
    // 开发期把 /api 代理到后端，前端代码里永远只写相对路径 ——
    // 换成生产环境（Nginx 反代）时一行都不用改。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8300',
        changeOrigin: true,
      },
    },
  },
})
