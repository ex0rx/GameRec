import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { redactAuthProxyError } from './proxyPrivacy.ts'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    server: {
      // The API owns credentialed CORS. Vite must not overlay its broader
      // development defaults or intercept the API's preflight decisions.
      cors: false,
      port: 5173,
      strictPort: true,
      allowedHosts: ['frontend'],
      watch: {
        ignored: [
          '**/test-results/**',
          '**/live-results/**',
          '**/playwright-report/**',
          '**/dist/**',
        ],
      },
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://api:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
          timeout: 200_000,
          proxyTimeout: 200_000,
          configure(proxy) {
            // Vite installs its default error logger after this hook.
            proxy.on('error', redactAuthProxyError)
          },
        },
      },
    },
  }
})
