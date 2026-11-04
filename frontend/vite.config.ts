import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// O navegador só fala com o Vite; /api segue para o backend (SSE incluído).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': { target: process.env.VITE_API_TARGET ?? 'http://localhost:8000', changeOrigin: false } } },
})
