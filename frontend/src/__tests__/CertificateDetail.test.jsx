import React from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import CertificateDetail from '../pages/CertificateDetail.jsx'
import { cert, mockRoutes, renderPage } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

const FINDINGS = [
  { check: 'expiring', severity: 'warning', message: 'Certificate expires in 12 day(s)' },
  { check: 'self_signed', severity: 'error', message: 'Certificate is self-signed and not in the trust store' },
]
const RISK = {
  probability: 0.081, tier: 'low', model_used: 'gradient_boosting', data_source: 'synthetic', floor_reason: null,
  factors: [{ feature: 'issuer_type', value: 'private_ca', typical: 'acme_public', effect: 0.053 }],
}
const PLAN = {
  id: 'p1', provider: 'letsencrypt', executable: true, status: 'pending_approval', not_executable_reason: null,
  steps: ['Register an ACME account', 'Create an order'], warnings: ['This is a TEST CA'],
  expires_at: new Date(Date.now() + 900000).toISOString(),
}

function routes(over = {}, certOver = {}) {
  mockRoutes(api.get, {
    '/certificates/c1': cert(certOver),
    '/certificates/c1/validation': [{ id: 'v1', overall: 'fail', checked_at: '2026-06-01T00:00:00Z', findings: FINDINGS }],
    '/risk/certificates/c1': RISK,
    '/renewals': [{ id: 't1', created_at: '2026-06-01T00:00:00Z', status: 'dry_run', provider: 'letsencrypt', triggered_by: 'user:alice' }],
    ...over,
  })
}

const open = (role = 'admin') => renderPage(<CertificateDetail />, { route: '/certificates/c1', path: '/certificates/:id', role })

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset()
})

describe('CertificateDetail: loading, error and loaded states (regression: the page used to crash before data arrived)', () => {
  it('shows a loading state and renders no certificate data until the API responds', async () => {
    api.get.mockImplementation(() => new Promise(() => {})) // never resolves
    open()
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
    expect(screen.queryByText(/Plan renewal/)).not.toBeInTheDocument()
  })

  it('shows the error and a way back when the certificate cannot be loaded', async () => {
    api.get.mockRejectedValue({ response: { data: { detail: 'Certificate not found' } } })
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Certificate not found')
    expect(screen.getByRole('link', { name: 'Back to certificates' })).toHaveAttribute('href', '/certificates')
  })

  it('renders details, validation findings, risk explanation and history once loaded', async () => {
    routes()
    open()
    expect(await screen.findByRole('heading', { level: 1, name: 'pay-api.prod.example.com' })).toBeInTheDocument()
    expect(screen.getByText('[error] Certificate is self-signed and not in the trust store')).toBeInTheDocument()
    expect(screen.getByText('[warning] Certificate expires in 12 day(s)')).toBeInTheDocument()
    expect(screen.getByText('8.1%')).toBeInTheDocument()
    expect(screen.getByText(/trained on synthetic data/)).toBeInTheDocument()
    expect(screen.getByText(/issuer_type=private_ca \(\+5\.3pp\)/)).toBeInTheDocument()
    const history = screen.getByRole('region', { name: 'Renewal history' })
    expect(within(history).getByText(/dry run/)).toBeInTheDocument()
  })

  it('shows "not determined" instead of guessing chain and revocation status', async () => {
    routes({}, { chain_valid: null, revoked: null })
    open()
    await screen.findByRole('heading', { level: 1 })
    expect(screen.getAllByText('not determined')).toHaveLength(2)
  })
})

describe('CertificateDetail: roles', () => {
  it('shows operators the action buttons', async () => {
    routes({}, { renewal_provider: 'letsencrypt', auto_renew_enabled: true })
    open('operator')
    expect(await screen.findByRole('button', { name: /Plan renewal via letsencrypt/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Re-validate/ })).toBeInTheDocument()
  })

  it('hides every write control from viewers', async () => {
    routes({}, { renewal_provider: 'letsencrypt', auto_renew_enabled: true })
    open('viewer')
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('button', { name: /Plan renewal/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Re-validate/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Create renewal ticket/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Disable auto-renew/ })).not.toBeInTheDocument()
  })
})

describe('CertificateDetail: plan, approve, execute', () => {
  const withProvider = () => routes({}, { renewal_provider: 'letsencrypt', auto_renew_enabled: true })

  it('a dry run shows the steps and warnings and calls only the plan endpoint', async () => {
    withProvider()
    api.post.mockResolvedValue({ data: PLAN })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Plan renewal via letsencrypt/ }))
    const plan = await screen.findByRole('region', { name: 'Renewal plan' })
    expect(within(plan).getByText('Register an ACME account')).toBeInTheDocument()
    expect(within(plan).getByText(/Warning: This is a TEST CA/)).toBeInTheDocument()
    expect(api.post.mock.calls.map((c) => c[0])).toEqual(['/renewals/c1/plan'])
  })

  it('a plan that cannot run disables approval and explains why', async () => {
    withProvider()
    api.post.mockResolvedValue({ data: { ...PLAN, executable: false, not_executable_reason: 'ACME directory unreachable' } })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Plan renewal/ }))
    expect(await screen.findByText(/Cannot be executed: ACME directory unreachable/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve & execute/ })).toBeDisabled()
  })

  it('approve & execute calls approve then execute, in that order, after a confirmation', async () => {
    withProvider()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.post.mockImplementation(async (url) => {
      if (url.endsWith('/plan')) return { data: PLAN }
      if (url.endsWith('/approve')) return { data: { ...PLAN, status: 'approved' } }
      return { data: { status: 'auto_renewed', new_serial: '5de9eb2ece1234' } }
    })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Plan renewal/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Approve & execute/ }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Renewal auto_renewed (new serial 5de9eb2ece…)'))
    expect(api.post.mock.calls.map((c) => c[0])).toEqual(['/renewals/c1/plan', '/renewals/plans/p1/approve', '/renewals/plans/p1/execute'])
    expect(screen.queryByRole('region', { name: 'Renewal plan' })).not.toBeInTheDocument()
  })

  it('declining the confirmation does not approve or execute anything', async () => {
    withProvider()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    api.post.mockResolvedValue({ data: PLAN })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Plan renewal/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Approve & execute/ }))
    expect(api.post.mock.calls.map((c) => c[0])).toEqual(['/renewals/c1/plan'])
  })

  it('shows the server message when approval or execution is refused', async () => {
    withProvider()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.post.mockImplementation(async (url) => {
      if (url.endsWith('/plan')) return { data: PLAN }
      throw { response: { data: { detail: { code: 'plan_expired', message: 'The approval expired; create and approve a new plan' } } } }
    })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Plan renewal/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Approve & execute/ }))
    expect(await screen.findByRole('status')).toHaveTextContent('The approval expired')
  })

  it('reject calls the reject endpoint and closes the plan', async () => {
    withProvider()
    api.post.mockImplementation(async (url) => (url.endsWith('/plan') ? { data: PLAN } : { data: {} }))
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Plan renewal/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Renewal plan' })).not.toBeInTheDocument())
    expect(api.post).toHaveBeenLastCalledWith('/renewals/plans/p1/reject')
  })
})
