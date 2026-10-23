import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// En desarrollo la tienda (localhost:5173) reenvía /marketplace/* al RAG, así el navegador
// no hace peticiones a otro origen y no hace falta configurar CORS. RAG_DEV_TARGET va en .env
const env = loadEnv(process.env.NODE_ENV || 'development', process.cwd(), '')
const ragDev = (env.RAG_DEV_TARGET || 'http://127.0.0.1:8000').replace(/\/+$/, '')

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/marketplace': { target: ragDev, changeOrigin: true },
    },
  },
  build: {
    sourcemap: false, // Impide reconstruir tus archivos .vue y .ts en la pestaña Sources
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true, // Remueve todos los console.log de la build final
        drop_debugger: true,
      },
      format: {
        comments: false, // Elimina los comentarios del código
      },
    },
  },
})
