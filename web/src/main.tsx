import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Toaster } from 'sonner'
import './index.css'
import App from './App'
import { TooltipProvider } from './components/ui'
import './lib/monaco'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TooltipProvider>
      <App />
      <Toaster position="bottom-right" toastOptions={{ className: '!bg-surface-2 !border-line !text-fg !text-xs !backdrop-blur-xl' }} />
    </TooltipProvider>
  </StrictMode>,
)
