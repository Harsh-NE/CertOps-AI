import React, { useEffect, useState } from 'react'
import api from '../api.js'
import { ShieldCheck } from 'lucide-react'

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isRegister, setIsRegister] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [canRegister, setCanRegister] = useState(false)

  useEffect(() => {
    api.get('/health').then((r) => setCanRegister(!!r.data.registration_enabled)).catch(() => {})
  }, [])

  async function submit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      let resp
      if (isRegister) {
        resp = await api.post('/auth/register', { username, password })
      } else {
        const form = new URLSearchParams()
        form.append('username', username)
        form.append('password', password)
        resp = await api.post('/auth/login', form, {
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        })
      }
      onLogin(resp.data.access_token)
    } catch (err) {
      setError(err.response?.data?.detail || 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4">
        <div className="flex items-center gap-2 mb-2">
          <ShieldCheck className="text-accent" size={28} />
          <h1 className="text-lg font-semibold">Cert Lifecycle Platform</h1>
        </div>
        <p className="text-sm text-muted">
          {isRegister ? 'Create a read-only account.' : 'Sign in to continue.'}
        </p>
        <input
          className="w-full bg-panel2 border border-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          type="password"
          className="w-full bg-panel2 border border-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <button
          disabled={loading}
          className="w-full bg-accent text-bg font-medium rounded-lg py-2 text-sm hover:opacity-90 transition"
        >
          {loading ? 'Please wait…' : isRegister ? 'Create account' : 'Sign in'}
        </button>
        {canRegister && (
          <button
          type="button"
          onClick={() => setIsRegister(!isRegister)}
          className="w-full text-xs text-muted hover:text-accent transition"
        >
          {isRegister ? 'Already have an account? Sign in' : 'Need an account? Sign up'}
        </button>
        )}
      </form>
    </div>
  )
}
