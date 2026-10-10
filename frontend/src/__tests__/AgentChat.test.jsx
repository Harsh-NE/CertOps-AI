import React from 'react'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api.js'
import AgentChat from '../pages/AgentChat.jsx'
import { mockRoutes, renderPage } from '../test/utils.jsx'

vi.mock('../api.js', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

const answer = (over = {}) => ({
  answer: '2 matching certificate(s): pay-api.prod.example.com, checkout.prod.example.com', used_tools: ['search_certificates'],
  tool_calls: [{ tool: 'search_certificates', status: 'ok' }], pending_plans: 0, configured: true, ...over,
})

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset()
  mockRoutes(api.get, { '/health': { status: 'ok', ai_agent_enabled: true } })
})

const ask = async (text) => {
  await userEvent.type(screen.getByLabelText('Message'), text)
  await userEvent.click(screen.getByRole('button', { name: 'Send' }))
}

describe('AgentChat', () => {
  it('warns when the agent is not configured', async () => {
    mockRoutes(api.get, { '/health': { status: 'ok', ai_agent_enabled: false } })
    renderPage(<AgentChat />, { route: '/agent' })
    expect(await screen.findByRole('status')).toHaveTextContent('GEMINI_API_KEY')
  })

  it('does not show the warning when the agent is configured', async () => {
    renderPage(<AgentChat />, { route: '/agent' })
    await screen.findByText(/I only answer from the platform's data/)
    expect(screen.queryByText(/not configured/)).not.toBeInTheDocument()
  })

  it('sends the question and shows the answer with the tools that were used', async () => {
    api.post.mockResolvedValue({ data: answer() })
    renderPage(<AgentChat />, { route: '/agent' })
    await ask('Which prod certs expire in 14 days?')
    expect(api.post).toHaveBeenCalledWith('/agent/query', { question: 'Which prod certs expire in 14 days?' })
    expect(await screen.findByText(/2 matching certificate/)).toBeInTheDocument()
    expect(screen.getByLabelText('Tools used')).toHaveTextContent('search_certificates: ok')
  })

  it('highlights denied tool calls and points to the approval queue when a plan was created', async () => {
    api.post.mockResolvedValue({ data: answer({
      answer: 'Plan p1 is pending_approval.', pending_plans: 1,
      tool_calls: [{ tool: 'plan_renewal', status: 'ok' }, { tool: 'execute_renewal', status: 'denied' }],
    }) })
    renderPage(<AgentChat />, { route: '/agent' })
    await ask('Renew pay-api now')
    const denied = await screen.findByText('execute_renewal: denied')
    expect(denied.className).toMatch(/text-danger/)
    expect(screen.getByRole('link', { name: 'Renewals page' })).toHaveAttribute('href', '/renewals')
  })

  it('shows a readable error when the request fails', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'Could not validate credentials' } } })
    renderPage(<AgentChat />, { route: '/agent' })
    await ask('hello')
    expect(await screen.findByText('Could not validate credentials')).toBeInTheDocument()
  })

  it('disables sending while a request is in flight and shows progress', async () => {
    let resolve
    api.post.mockImplementation(() => new Promise((r) => { resolve = r }))
    renderPage(<AgentChat />, { route: '/agent' })
    await ask('slow question')
    expect(await screen.findByText('Thinking…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    resolve({ data: answer({ answer: 'done' }) })
    expect(await screen.findByText('done')).toBeInTheDocument()
  })

  it('ignores empty questions', async () => {
    renderPage(<AgentChat />, { route: '/agent' })
    await screen.findByText(/I only answer/)
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('sends a suggested question when clicked', async () => {
    api.post.mockResolvedValue({ data: answer() })
    renderPage(<AgentChat />, { route: '/agent' })
    await userEvent.click(await screen.findByRole('button', { name: 'Show me all expired certificates' }))
    expect(api.post).toHaveBeenCalledWith('/agent/query', { question: 'Show me all expired certificates' })
  })

  it('remembers the conversation across reloads and can clear it', async () => {
    api.post.mockResolvedValue({ data: answer({ answer: 'remembered answer' }) })
    const first = renderPage(<AgentChat />, { route: '/agent' })
    await ask('hello')
    await screen.findByText('remembered answer')
    first.unmount()
    renderPage(<AgentChat />, { route: '/agent' })
    expect(await screen.findByText('remembered answer')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear conversation' }))
    await waitFor(() => expect(screen.queryByText('remembered answer')).not.toBeInTheDocument())
  })
})
