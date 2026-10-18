import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const gateway = process.env.GATEWAY_URL ?? 'http://localhost:8000'
const desk = process.env.DESK_URL ?? 'http://localhost:8200'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': gateway, '/paypal': gateway, '/desk': desk },
  },
})
