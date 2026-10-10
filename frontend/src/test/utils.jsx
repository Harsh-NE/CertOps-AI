import React from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthContext } from '../auth.jsx'

/** Render a page as a given role at a route. role: 'admin' | 'operator' | 'viewer'. */
export function renderPage(ui, { route = '/', path = route, role = 'admin' } = {}) {
  const user = { username: 'tester', role }
  const value = { user, isOperator: role !== 'viewer' }
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthContext.Provider value={value}>
        <Routes>
          <Route path={path} element={ui} />
          <Route path="*" element={<p>other route</p>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

/** Route table for a mocked axios instance: { '/path': data | (config, url) => data | {data, headers} }. */
export function mockRoutes(fn, routes) {
  fn.mockImplementation((url, config) => {
    const key = url.split('?')[0]
    const handler = routes[key]
    if (handler === undefined) return Promise.reject(new Error(`unmocked request: ${url}`))
    const out = typeof handler === 'function' ? handler(config, url) : handler
    if (out instanceof Promise) return out
    const res = out && out.__response ? out : { data: out, headers: {} }
    return Promise.resolve(res)
  })
}

export const withHeaders = (data, headers) => ({ __response: true, data, headers })

export const cert = (over = {}) => ({
  id: 'c1', common_name: 'pay-api.prod.example.com', expected_hostname: null, environment: 'prod', status: 'valid',
  source: 'network_scan', source_ref: '10.0.0.1:443', issuer: 'CN=R11,O=Lets Encrypt', subject: 'CN=pay-api.prod.example.com',
  not_before: '2026-01-01T00:00:00Z', not_after: new Date(Date.now() + 40 * 86400000).toISOString(),
  key_algorithm: 'RSA', key_size: 2048, signature_algorithm: 'sha256WithRSAEncryption', chain_valid: true, revoked: null,
  risk_tier: 'low', risk_score: 4.2, predicted_outage_impact: 'low', auto_renew_enabled: false, renewal_provider: null,
  owner: null, ...over,
})
