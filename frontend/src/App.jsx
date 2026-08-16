import React, { useEffect, useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Certificates from './pages/Certificates.jsx'
import CertificateDetail from './pages/CertificateDetail.jsx'
import Renewals from './pages/Renewals.jsx'
import AgentChat from './pages/AgentChat.jsx'
import Settings from './pages/Settings.jsx'
import Login from './pages/Login.jsx'

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('token'))

  useEffect(() => {
    const onStorage = () => setToken(localStorage.getItem('token'))
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  if (!token) {
    return <Login onLogin={(t) => { localStorage.setItem('token', t); setToken(t) }} />
  }

  return (
    <div className="min-h-screen bg-bg">
      <Navbar onLogout={() => { localStorage.removeItem('token'); setToken(null) }} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/certificates" element={<Certificates />} />
          <Route path="/certificates/:id" element={<CertificateDetail />} />
          <Route path="/renewals" element={<Renewals />} />
          <Route path="/agent" element={<AgentChat />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  )
}
