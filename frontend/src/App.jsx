import React, { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth.jsx'
import Navbar from './components/Navbar.jsx'
import AgentChat from './pages/AgentChat.jsx'
import CertificateDetail from './pages/CertificateDetail.jsx'
import Certificates from './pages/Certificates.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Login from './pages/Login.jsx'
import Renewals from './pages/Renewals.jsx'
import Sources from './pages/Settings.jsx'

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('token'))

  // api.js dispatches "storage" when a 401 clears the token, which returns the user to the login screen.
  useEffect(() => {
    const onStorage = () => setToken(localStorage.getItem('token'))
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  if (!token) {
    return <Login onLogin={(t) => { localStorage.setItem('token', t); setToken(t) }} />
  }

  return (
    <AuthProvider token={token}>
      <div className="min-h-screen bg-bg">
        <Navbar onLogout={() => { localStorage.removeItem('token'); setToken(null) }} />
        <main className="max-w-7xl mx-auto px-6 py-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/certificates" element={<Certificates />} />
            <Route path="/certificates/:id" element={<CertificateDetail />} />
            <Route path="/renewals" element={<Renewals />} />
            <Route path="/agent" element={<AgentChat />} />
            <Route path="/sources" element={<Sources />} />
            <Route path="/settings" element={<Navigate to="/sources" replace />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
    </AuthProvider>
  )
}
