import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import App from '../App.jsx'
import Login from '../pages/Login.jsx'
import { mockRoutes } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

beforeEach(() => { api.get.mockReset(); api.post.mockReset() })

describe('Login', () => {
  it('offers sign-up only when the server allows registration', async () => {
    mockRoutes(api.get, { '/health': { registration_enabled: false } })
    const { unmount } = render(<Login onLogin={() => {}} />)
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Sign up/ })).not.toBeInTheDocument()
    unmount()
    mockRoutes(api.get, { '/health': { registration_enabled: true } })
    render(<Login onLogin={() => {}} />)
    expect(await screen.findByRole('button', { name: /Sign up/ })).toBeInTheDocument()
  })

  it('posts credentials as a form and hands the token to the app', async () => {
    mockRoutes(api.get, { '/health': { registration_enabled: false } })
    api.post.mockResolvedValue({ data: { access_token: 'tok123' } })
    const onLogin = vi.fn()
    render(<Login onLogin={onLogin} />)
    await userEvent.type(screen.getByPlaceholderText('Username'), 'admin')
    await userEvent.type(screen.getByPlaceholderText('Password'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith('tok123'))
    const [url, body] = api.post.mock.calls[0]
    expect(url).toBe('/auth/login')
    expect(body.get('username')).toBe('admin')
    expect(body.get('password')).toBe('secret')
  })

  it('shows the server message for bad credentials and does not log in', async () => {
    mockRoutes(api.get, { '/health': { registration_enabled: false } })
    api.post.mockRejectedValue({ response: { data: { detail: 'Incorrect username or password' } } })
    const onLogin = vi.fn()
    render(<Login onLogin={onLogin} />)
    await userEvent.type(screen.getByPlaceholderText('Username'), 'admin')
    await userEvent.type(screen.getByPlaceholderText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Incorrect username or password')).toBeInTheDocument()
    expect(onLogin).not.toHaveBeenCalled()
  })
})

describe('App', () => {
  const emptyApi = () => mockRoutes(api.get, {
    '/auth/me': { username: 'alice', role: 'operator' }, '/health': { registration_enabled: false },
    '/risk/dashboard': { total_certificates: 0, expiring_7d: 0, expired: 0, invalid_chain: 0, high_risk: 0 },
    '/risk/expiry-timeline': { buckets: [] }, '/certificates': [], '/jobs': [], '/renewals/plans': [],
  })

  it('shows the login screen without a token', () => {
    mockRoutes(api.get, { '/health': { registration_enabled: false } })
    render(<MemoryRouter><App /></MemoryRouter>)
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })

  it('shows the app with the signed-in user and role once a token exists', async () => {
    localStorage.setItem('token', 'abc')
    emptyApi()
    render(<MemoryRouter><App /></MemoryRouter>)
    expect(await screen.findByText('alice · operator')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
    for (const name of ['Dashboard', 'Certificates', 'Renewals', 'Agent', 'Sources']) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument()
    }
  })

  it('logging out clears the token and returns to the login screen', async () => {
    localStorage.setItem('token', 'abc')
    emptyApi()
    render(<MemoryRouter><App /></MemoryRouter>)
    await userEvent.click(await screen.findByRole('button', { name: /Logout/ }))
    expect(localStorage.getItem('token')).toBeNull()
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('returns to the login screen when another part of the app clears an expired token', async () => {
    localStorage.setItem('token', 'abc')
    emptyApi()
    render(<MemoryRouter><App /></MemoryRouter>)
    await screen.findByText('alice · operator')
    localStorage.removeItem('token')
    window.dispatchEvent(new Event('storage'))
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('redirects the old /settings URL to /sources', async () => {
    localStorage.setItem('token', 'abc')
    emptyApi()
    mockRoutes(api.get, { '/auth/me': { username: 'alice', role: 'viewer' }, '/targets': [], '/jobs': [] })
    render(<MemoryRouter initialEntries={['/settings']}><App /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: /Sources/ })).toBeInTheDocument()
  })
})
