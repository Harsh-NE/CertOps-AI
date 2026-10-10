import { afterEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'

function respondWith(status) {
  api.defaults.adapter = async (config) => {
    const response = { status, data: {}, headers: {}, config }
    if (status >= 400) {
      const err = new Error(`HTTP ${status}`)
      err.response = response
      err.config = config
      throw err
    }
    return response
  }
}

afterEach(() => { delete api.defaults.adapter })

describe('api client', () => {
  it('sends the stored bearer token', async () => {
    localStorage.setItem('token', 'abc123')
    let seen
    api.defaults.adapter = async (config) => { seen = config.headers.Authorization; return { status: 200, data: {}, headers: {}, config } }
    await api.get('/health')
    expect(seen).toBe('Bearer abc123')
  })

  it('sends no Authorization header when signed out', async () => {
    let seen = 'unset'
    api.defaults.adapter = async (config) => { seen = config.headers.Authorization; return { status: 200, data: {}, headers: {}, config } }
    await api.get('/health')
    expect(seen).toBeUndefined()
  })

  it('clears the token and notifies the app on a 401', async () => {
    localStorage.setItem('token', 'expired')
    const onStorage = vi.fn()
    window.addEventListener('storage', onStorage)
    respondWith(401)
    await expect(api.get('/certificates')).rejects.toBeTruthy()
    window.removeEventListener('storage', onStorage)
    expect(localStorage.getItem('token')).toBeNull()
    expect(onStorage).toHaveBeenCalled()
  })

  it('keeps the token on other errors', async () => {
    localStorage.setItem('token', 'keep')
    respondWith(500)
    await expect(api.get('/certificates')).rejects.toBeTruthy()
    expect(localStorage.getItem('token')).toBe('keep')
  })
})
