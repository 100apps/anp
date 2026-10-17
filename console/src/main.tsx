import { createRoot } from 'react-dom/client'
import { QueryClient,QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { AuthProvider } from './auth/auth-provider'
import App from './app'
import './styles/globals.css'

const queries=new QueryClient({defaultOptions:{queries:{retry:1,staleTime:1000}}})
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={queries}><AuthProvider><App/><Toaster richColors position="top-right"/></AuthProvider></QueryClientProvider>)
