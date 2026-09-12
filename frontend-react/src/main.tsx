import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import '@/styles/global.css'
import AuthProvider from '@/hooks/AuthProvider'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
)
