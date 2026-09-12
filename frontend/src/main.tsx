import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { App } from '@/app/App'
import { AuthProvider } from '@/auth/AuthProvider'
import { ApiProblem } from '@/api/problem'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A refusal is an answer, not a hiccup. Retrying a 403 or a 404 changes nothing and delays
      // the message the person needs to read.
      retry: (failureCount, error) =>
        !(error instanceof ApiProblem && error.status < 500) && failureCount < 2,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
