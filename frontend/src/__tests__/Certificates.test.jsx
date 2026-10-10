import React from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import Certificates from '../pages/Certificates.jsx'
import { cert, mockRoutes, renderPage, withHeaders } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

const query = (url) => Object.fromEntries(new URLSearchParams(url.split('?')[1] || ''))
const calls = () => api.get.mock.calls.map(([url]) => query(url))
const last = () => calls().at(-1)

function listing(rows, total = rows.length) {
  mockRoutes(api.get, { '/certificates': () => withHeaders(rows, { 'x-total-count': String(total) }) })
}

beforeEach(() => { api.get.mockReset(); api.post.mockReset() })

describe('Certificates', () => {
  it('loads the first page from the server with limit and offset', async () => {
    listing([cert(), cert({ id: 'c2', common_name: 'checkout.prod.example.com', status: 'expiring_soon', risk_tier: 'high' })])
    renderPage(<Certificates />, { route: '/certificates' })
    expect(await screen.findByRole('link', { name: 'pay-api.prod.example.com' })).toHaveAttribute('href', '/certificates/c1')
    expect(last()).toEqual({ limit: '25', offset: '0' })
    const table = within(screen.getByRole('table'))
    expect(table.getByText('expiring soon')).toBeInTheDocument()
    expect(table.getByText('HIGH')).toBeInTheDocument()
    expect(screen.getByText('1–2 of 2')).toBeInTheDocument()
  })

  it('sends each filter to the server and resets to the first page', async () => {
    listing([cert()], 60)
    renderPage(<Certificates />, { route: '/certificates' })
    await screen.findByRole('link', { name: /pay-api/ })
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(last().offset).toBe('25'))
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'expired')
    await waitFor(() => expect(last()).toMatchObject({ status: 'expired', offset: '0' }))
    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'critical')
    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'prod')
    await userEvent.selectOptions(screen.getByLabelText('Expires within'), '14')
    await waitFor(() => expect(last()).toMatchObject({ status: 'expired', risk_tier: 'critical', environment: 'prod', expires_within_days: '14' }))
  })

  it('debounces free-text search into a single request', async () => {
    listing([cert()])
    renderPage(<Certificates />, { route: '/certificates' })
    await screen.findByRole('link', { name: /pay-api/ })
    const before = calls().length
    await userEvent.type(screen.getByLabelText('Search'), 'pay')
    await waitFor(() => expect(last().q).toBe('pay'))
    expect(calls().length - before).toBeLessThanOrEqual(2)
  })

  it('pages with the total from X-Total-Count', async () => {
    listing([cert()], 60)
    renderPage(<Certificates />, { route: '/certificates' })
    expect(await screen.findByText('1–25 of 60')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('26–50 of 60')).toBeInTheDocument()
  })

  it('offers to clear filters and clears them', async () => {
    listing([cert()])
    renderPage(<Certificates />, { route: '/certificates' })
    await screen.findByRole('link', { name: /pay-api/ })
    expect(screen.queryByText('Clear filters')).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'valid')
    await userEvent.click(await screen.findByText('Clear filters'))
    await waitFor(() => expect(last()).toEqual({ limit: '25', offset: '0' }))
  })

  it('distinguishes "no matches" from "no certificates yet"', async () => {
    listing([], 0)
    renderPage(<Certificates />, { route: '/certificates' })
    expect(await screen.findByText(/No certificates yet/)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'expired')
    expect(await screen.findByText(/No certificates match these filters/)).toBeInTheDocument()
  })

  it('shows an error when the list cannot be loaded', async () => {
    api.get.mockRejectedValue({ response: { data: { detail: 'database unavailable' } } })
    renderPage(<Certificates />, { route: '/certificates' })
    expect(await screen.findByRole('alert')).toHaveTextContent('database unavailable')
  })

  it('lets operators scan targets and refreshes the list', async () => {
    listing([cert()])
    api.post.mockResolvedValue({ data: [cert()] })
    renderPage(<Certificates />, { route: '/certificates', role: 'operator' })
    await screen.findByRole('link', { name: /pay-api/ })
    await userEvent.type(screen.getByLabelText('Scan target'), '10.0.0.1:443#a.example.com, 10.0.0.2')
    await userEvent.click(screen.getByRole('button', { name: 'Scan now' }))
    expect(api.post).toHaveBeenCalledWith('/certificates/scan', { targets: ['10.0.0.1:443#a.example.com', '10.0.0.2'] })
    expect(await screen.findByRole('status')).toHaveTextContent('Scanned 1 endpoint(s).')
  })

  it('shows the server error when a scan fails', async () => {
    listing([cert()])
    api.post.mockRejectedValue({ response: { data: { detail: { errors: ['x'], message: 'not a public address' } } } })
    renderPage(<Certificates />, { route: '/certificates', role: 'admin' })
    await screen.findByRole('link', { name: /pay-api/ })
    await userEvent.type(screen.getByLabelText('Scan target'), '169.254.169.254:80')
    await userEvent.click(screen.getByRole('button', { name: 'Scan now' }))
    expect(await screen.findByRole('status')).toHaveTextContent('not a public address')
  })

  it('hides the scan form from viewers', async () => {
    listing([cert()])
    renderPage(<Certificates />, { route: '/certificates', role: 'viewer' })
    await screen.findByRole('link', { name: /pay-api/ })
    expect(screen.queryByLabelText('Scan target')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Scan now' })).not.toBeInTheDocument()
  })

  it('shows the hostname under a differing common name', async () => {
    listing([cert({ common_name: 'some-other-name.lab.test', expected_hostname: 'wrong-host.lab.test' })])
    renderPage(<Certificates />, { route: '/certificates' })
    const row = (await screen.findByRole('link', { name: 'some-other-name.lab.test' })).closest('tr')
    expect(within(row).getByText('wrong-host.lab.test')).toBeInTheDocument()
  })
})
