import React from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import Dashboard from '../pages/Dashboard.jsx'
import { cert, mockRoutes, renderPage } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

const STATS = { total_certificates: 11, expiring_30d: 2, expiring_7d: 1, expired: 1, invalid_chain: 3, high_risk: 7, auto_renew_enabled: 0, security_score: 80 }
const BUCKETS = [{ label: 'expired', low: 0, medium: 0, high: 0, critical: 1, unscored: 0, total: 1 }]
const soon = (days) => new Date(Date.now() + days * 86400000).toISOString()

let certs
beforeEach(() => {
  api.get.mockReset()
  certs = [
    cert({ id: 'a', common_name: 'expired.lab.test', status: 'expired', risk_tier: 'critical', not_after: soon(-11) }),
    cert({ id: 'b', common_name: 'healthy.lab.test', status: 'valid', risk_tier: 'low', not_after: soon(200) }),
    cert({ id: 'c', common_name: 'selfsigned.lab.test', status: 'invalid_chain', risk_tier: 'high', not_after: soon(80) }),
  ]
  mockRoutes(api.get, {
    '/risk/dashboard': STATS,
    '/risk/expiry-timeline': { buckets: BUCKETS },
    '/certificates': () => certs,
    '/jobs': [{ id: 'j1', provider: 'network_scan', status: 'succeeded', discovered: 11, started_at: '2026-06-01T10:00:00Z', errors: [] }],
    '/renewals/plans': [{ id: 'p1' }, { id: 'p2' }],
  })
})

describe('Dashboard', () => {
  it('shows the headline counts, including plans awaiting approval', async () => {
    renderPage(<Dashboard />, { route: '/' })
    expect(await screen.findByText('Certificates')).toBeInTheDocument()
    const card = (label) => screen.getByText(label).closest('div.card')
    expect(within(card('Certificates')).getByText('11')).toBeInTheDocument()
    expect(within(card('Expired')).getByText('1')).toBeInTheDocument()
    expect(within(card('High / critical')).getByText('7')).toBeInTheDocument()
    expect(within(card('Plans awaiting approval')).getByText('2')).toBeInTheDocument()
  })

  it('lists only certificates that need attention, worst first, with links', async () => {
    renderPage(<Dashboard />, { route: '/' })
    const section = (await screen.findByRole('heading', { name: 'Needs attention' })).closest('div.card')
    expect(within(section).getByRole('link', { name: 'expired.lab.test' })).toHaveAttribute('href', '/certificates/a')
    expect(within(section).getByRole('link', { name: 'selfsigned.lab.test' })).toBeInTheDocument()
    expect(within(section).queryByText('healthy.lab.test')).not.toBeInTheDocument()
  })

  it('shows discovery health', async () => {
    renderPage(<Dashboard />, { route: '/' })
    expect(await screen.findByText('network_scan')).toBeInTheDocument()
    expect(screen.getByText(/11 found/)).toBeInTheDocument()
    expect(screen.getByText('succeeded')).toHaveClass('text-accent2')
  })

  it('refetches everything for one environment when the filter changes', async () => {
    renderPage(<Dashboard />, { route: '/' })
    await screen.findByText('Certificates')
    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'prod')
    await waitFor(() => {
      const timeline = api.get.mock.calls.filter(([u]) => u === '/risk/expiry-timeline').at(-1)
      expect(timeline[1].params).toEqual({ environment: 'prod' })
      const list = api.get.mock.calls.filter(([u]) => u === '/certificates').at(-1)
      expect(list[1].params).toMatchObject({ environment: 'prod', limit: 200 })
    })
  })

  it('has friendly empty states', async () => {
    certs = []
    mockRoutes(api.get, {
      '/risk/dashboard': { ...STATS, total_certificates: 0 }, '/risk/expiry-timeline': { buckets: [] },
      '/certificates': [], '/jobs': [], '/renewals/plans': [],
    })
    renderPage(<Dashboard />, { route: '/' })
    expect(await screen.findByText('Nothing urgent.')).toBeInTheDocument()
    expect(screen.getByText(/No discovery runs yet/)).toBeInTheDocument()
    expect(screen.getByText('No certificates yet.')).toBeInTheDocument()
  })

  it('shows an error instead of a broken page when the API fails', async () => {
    api.get.mockRejectedValue({ response: { data: { detail: 'Could not validate credentials' } } })
    renderPage(<Dashboard />, { route: '/' })
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not validate credentials')
  })

  it('says the risk tiers come from a synthetic-data model', async () => {
    renderPage(<Dashboard />, { route: '/' })
    expect(await screen.findByText(/trained on synthetic data/)).toBeInTheDocument()
  })
})
