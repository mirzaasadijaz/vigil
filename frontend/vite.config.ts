import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
  plugins: [
    react(),
    tailwindcss(),
  ],
  build: {
    watch: {
      exclude: ['node_modules/**', 'dist/**', '**/.git/**']
    }
  },
  server: {
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
        ws: true,
      },
      '/media': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
    },
  },
})
