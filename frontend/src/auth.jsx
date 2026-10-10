import React, { createContext, useContext, useEffect, useState } from 'react'
import api from './api.js'

const AuthContext = createContext({ user: null, isOperator: false })

/** Loads /auth/me once so the UI can hide actions the server would refuse anyway (the server stays the authority). */
export function AuthProvider({ token, children }) {
  const [user, setUser] = useState(null)
  useEffect(() => {
    if (!token) { setUser(null); return }
    api.get('/auth/me').then((r) => setUser(r.data)).catch(() => setUser(null))
  }, [token])
  const isOperator = !!user && ['operator', 'admin'].includes(user.role)
  return <AuthContext.Provider value={{ user, isOperator }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
