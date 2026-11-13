import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      allowedHosts: ['frontend'],
      watch: {
        ignored: ['**/test-results/**', '**/live-results/**', '**/playwright-report/**', '**/dist/**'],
      },
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://api:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
          timeout: 200_000,
          proxyTimeout: 200_000,
        },
      },
    },
  }
})
