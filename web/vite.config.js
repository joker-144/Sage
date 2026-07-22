import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// ── 统一版本号读取：根目录的 VERSION 文件 ──
function readVersion() {
  const candidates = [
    resolve(__dirname, '../VERSION'),
    resolve(__dirname, '../../VERSION'),
  ]
  for (const p of candidates) {
    if (existsSync(p)) {
      try {
        return readFileSync(p, 'utf-8').trim()
      } catch {
        // ignore
      }
    }
  }
  return '0.0.0'
}

const APP_VERSION = readVersion()

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [vue()],
    base: './',
    define: {
      __APP_VERSION__: JSON.stringify(APP_VERSION),
    },
    server: {
      port: 5173,
      // 禁用压缩防止 SSE 流被缓冲
      compress: false,
      proxy: {
        '/chat': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              // SSE 需要 keep-alive 和禁用缓存
              proxyReq.setHeader('Connection', 'keep-alive');
              proxyReq.setHeader('Accept', 'text/event-stream');
              proxyReq.setHeader('Cache-Control', 'no-store');
            });
          },
          proxyTimeout: 180000,
          timeout: 180000,
        },
        '/conversations': 'http://localhost:8000',
        '/index': 'http://localhost:8000',
        '/memory': 'http://localhost:8000',
        '/health': 'http://localhost:8000',
        '/api': 'http://localhost:8000',
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
