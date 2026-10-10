import React from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import Renewals from '../pages/Renewals.jsx'
import { mockRoutes, renderPage } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

const plan = (over = {}) => ({
  id: 'p1', certificate_id: 'c1', provider: 'letsencrypt', status: 'pending_approval', executable: true, not_executable_reason: null,
  steps: ['Register an ACME account', 'Create an order'], warnings: ['This is a TEST CA'], created_by: 'agent:alice',
  expires_at: new Date(Date.now() + 600000).toISOString(), ...over,
})
const task = (over = {}) => ({
  id: 't1', certificate_id: 'c1', certificate_name: 'pay-api.prod.example.com', created_at: '2026-06-01T10:00:00Z', provider: 'letsencrypt',
  status: 'auto_renewed', previous_serial: '2b54fc103e0099', new_serial: '5de9eb2ece7788', triggered_by: 'user:admin (approved by user:admin)',
  notes: '{"secret_ref": "vault:secret/certops/x"}', ...over,
})

let tasks, plans
beforeEach(() => {
  api.get.mockReset(); api.post.mockReset()
  tasks = [task(), task({ id: 't2', status: 'failed', new_serial: null, notes: 'ConnectionError: refused' })]
  plans = [plan()]
  mockRoutes(api.get, { '/renewals': () => tasks, '/renewals/plans': () => plans })
})

const filters = () => api.get.mock.calls.filter(([u]) => u.startsWith('/renewals?')).map(([u]) => Object.fromEntries(new URLSearchParams(u.split('?')[1])))

describe('Renewals: history', () => {
  it('lists attempts with certificate links and the serial change', async () => {
    renderPage(<Renewals />, { route: '/renewals' })
    const links = await screen.findAllByRole('link', { name: 'pay-api.prod.example.com' })
    expect(links[0]).toHaveAttribute('href', '/certificates/c1')
    expect(screen.getByText('2b54fc103e… → 5de9eb2ece…')).toBeInTheDocument()
    expect(within(screen.getByRole('table')).getByText('failed')).toHaveClass('text-danger')
  })

  it('filters by status and by dry runs on the server', async () => {
    renderPage(<Renewals />, { route: '/renewals' })
    await screen.findAllByRole('link', { name: /pay-api/ })
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'failed')
    await waitFor(() => expect(filters().at(-1)).toMatchObject({ status: 'failed', include_dry_runs: 'true' }))
    await userEvent.click(screen.getByLabelText('Show dry runs'))
    await waitFor(() => expect(filters().at(-1)).toMatchObject({ status: 'failed', include_dry_runs: 'false' }))
  })

  it('has an empty state', async () => {
    tasks = []
    renderPage(<Renewals />, { route: '/renewals' })
    expect(await screen.findByText('No renewal activity matches.')).toBeInTheDocument()
  })
})

describe('Renewals: plans awaiting a human decision', () => {
  it('shows steps, warnings and who created the plan', async () => {
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    const section = await screen.findByRole('region', { name: /Plans awaiting a human decision/ })
    expect(within(section).getByText('Register an ACME account')).toBeInTheDocument()
    expect(within(section).getByText(/by agent:alice/)).toBeInTheDocument()
    expect(within(section).getByText(/Warning: This is a TEST CA/)).toBeInTheDocument()
  })

  it('an operator approves and executes: approve first, then execute', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.post.mockResolvedValue({ data: {} })
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    await userEvent.click(await screen.findByRole('button', { name: /Approve & execute/ }))
    await waitFor(() => expect(api.post.mock.calls.map((c) => c[0])).toEqual(['/renewals/plans/p1/approve', '/renewals/plans/p1/execute']))
    expect(await screen.findByRole('status')).toHaveTextContent('Plan approved and executed.')
  })

  it('does nothing when the confirmation is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    await userEvent.click(await screen.findByRole('button', { name: /Approve & execute/ }))
    expect(api.post).not.toHaveBeenCalled()
  })

  it('rejecting calls only the reject endpoint', async () => {
    api.post.mockResolvedValue({ data: {} })
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(api.post.mock.calls.map((c) => c[0])).toEqual(['/renewals/plans/p1/reject']))
  })

  it('disables approval for a plan that cannot be executed and shows the reason', async () => {
    plans = [plan({ executable: false, not_executable_reason: 'ACM renews eligible certificates itself' })]
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    expect(await screen.findByText(/Cannot be executed: ACM renews/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve & execute/ })).toBeDisabled()
  })

  it('shows the server refusal (for example an expired approval) and refreshes', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.post.mockRejectedValue({ response: { data: { detail: { message: 'The plan expired; create a new one' } } } })
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    await userEvent.click(await screen.findByRole('button', { name: /Approve & execute/ }))
    expect(await screen.findByRole('status')).toHaveTextContent('The plan expired')
  })

  it('shows viewers the queue but no decision buttons', async () => {
    renderPage(<Renewals />, { route: '/renewals', role: 'viewer' })
    await screen.findByText('Register an ACME account')
    expect(screen.queryByRole('button', { name: /Approve & execute/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('only counts open plans as awaiting a decision', async () => {
    plans = [plan(), plan({ id: 'p2', status: 'executed' }), plan({ id: 'p3', status: 'rejected' })]
    renderPage(<Renewals />, { route: '/renewals', role: 'operator' })
    expect(await screen.findByRole('heading', { name: 'Plans awaiting a human decision (1)' })).toBeInTheDocument()
  })
})
