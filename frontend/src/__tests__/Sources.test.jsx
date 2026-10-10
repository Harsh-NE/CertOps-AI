import React from 'react'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import Sources from '../pages/Settings.jsx'
import { mockRoutes, renderPage } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

const TARGET = { id: 't1', host: '127.0.0.1', port: 9441, server_name: 'healthy.lab.test', environment: 'prod', last_scanned_at: '2026-06-01T10:00:00Z', last_error: null }
const JOB = { id: 'j1', provider: 'network_scan', status: 'partial', attempts: 2, discovered: 10, errors: ['127.0.0.1:1: refused'], triggered_by: 'user:admin', started_at: '2026-06-01T10:00:00Z' }

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset(); api.delete.mockReset()
  mockRoutes(api.get, { '/targets': [TARGET], '/jobs': [JOB] })
})

describe('Sources', () => {
  it('lists targets and recent runs, with run errors available as a tooltip', async () => {
    renderPage(<Sources />, { route: '/sources' })
    expect(await screen.findByText(/127\.0\.0\.1:9441/)).toBeInTheDocument()
    const row = screen.getByText('partial').closest('tr')
    expect(row).toHaveAttribute('title', '127.0.0.1:1: refused')
    expect(screen.getByText('partial')).toHaveClass('text-warn')
  })

  it('lets an operator add a target with the right payload', async () => {
    api.post.mockResolvedValue({ data: TARGET })
    renderPage(<Sources />, { route: '/sources', role: 'operator' })
    await screen.findByText(/127\.0\.0\.1:9441/)
    await userEvent.type(screen.getByLabelText('Host'), 'app.example.com')
    await userEvent.clear(screen.getByLabelText('Port'))
    await userEvent.type(screen.getByLabelText('Port'), '8443')
    await userEvent.type(screen.getByLabelText('Server name'), 'app.example.com')
    await userEvent.selectOptions(screen.getByLabelText('Target environment'), 'staging')
    await userEvent.click(screen.getByRole('button', { name: 'Add target' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/targets',
      { host: 'app.example.com', port: 8443, server_name: 'app.example.com', environment: 'staging' }))
  })

  it('shows the server error when adding a target fails', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'invalid host' } } })
    renderPage(<Sources />, { route: '/sources', role: 'admin' })
    await screen.findByText(/127\.0\.0\.1:9441/)
    await userEvent.type(screen.getByLabelText('Host'), 'bad host')
    await userEvent.click(screen.getByRole('button', { name: 'Add target' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid host')
  })

  it('removes a target', async () => {
    api.delete.mockResolvedValue({})
    renderPage(<Sources />, { route: '/sources', role: 'operator' })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove 127.0.0.1:9441' }))
    expect(api.delete).toHaveBeenCalledWith('/targets/t1')
  })

  it('runs discovery for the chosen providers and reports each outcome', async () => {
    api.post.mockResolvedValue({ data: {
      network_scan: { status: 'succeeded', discovered: 11, errors: [] },
      aws_acm: { status: 'skipped', discovered: 0, errors: ['provider is not configured'] },
    } })
    renderPage(<Sources />, { route: '/sources', role: 'operator' })
    await screen.findByText(/127\.0\.0\.1:9441/)
    await userEvent.click(screen.getByLabelText('AWS Certificate Manager'))
    await userEvent.click(screen.getByRole('button', { name: 'Run discovery' }))
    expect(api.post).toHaveBeenCalledWith('/cloud/discover', { providers: ['network_scan', 'aws_acm'] })
    const result = await screen.findByLabelText('Discovery result')
    expect(result).toHaveTextContent('network_scan: succeeded, 11 found')
    expect(result).toHaveTextContent('aws_acm: skipped')
  })

  it('cannot run discovery with nothing selected', async () => {
    renderPage(<Sources />, { route: '/sources', role: 'operator' })
    await screen.findByText(/127\.0\.0\.1:9441/)
    await userEvent.click(screen.getByLabelText('Network scan targets', { selector: 'input' }))
    expect(screen.getByRole('button', { name: 'Run discovery' })).toBeDisabled()
  })

  it('gives viewers a read-only page', async () => {
    renderPage(<Sources />, { route: '/sources', role: 'viewer' })
    await screen.findByText(/127\.0\.0\.1:9441/)
    expect(screen.queryByRole('button', { name: 'Run discovery' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Host')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    expect(screen.getByText('Operators can run discovery.')).toBeInTheDocument()
  })
})
