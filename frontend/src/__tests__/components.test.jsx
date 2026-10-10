import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import CertCard from '../components/CertCard.jsx'
import ExpiryTimeline from '../components/ExpiryTimeline.jsx'
import Pager from '../components/Pager.jsx'
import RiskBadge from '../components/RiskBadge.jsx'
import StatCard from '../components/StatCard.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { cert } from '../test/utils.jsx'

describe('StatusBadge', () => {
  it('shows a human label', () => {
    render(<StatusBadge status="invalid_chain" />)
    expect(screen.getByText('invalid chain')).toBeInTheDocument()
  })
  it('colours failing and healthy statuses differently', () => {
    const { rerender } = render(<StatusBadge status="expired" />)
    expect(screen.getByText('expired').className).toMatch(/text-danger/)
    rerender(<StatusBadge status="valid" />)
    expect(screen.getByText('valid').className).toMatch(/text-accent2/)
  })
})

describe('RiskBadge', () => {
  it('upper-cases the tier and exposes the score as a tooltip', () => {
    render(<RiskBadge tier="high" score={50} />)
    expect(screen.getByText('HIGH')).toHaveAttribute('title', 'risk score 50')
  })
  it('marks missing tiers as unscored', () => {
    render(<RiskBadge tier={null} />)
    expect(screen.getByText('UNSCORED')).toBeInTheDocument()
  })
})

describe('Pager', () => {
  it('shows the visible range and disables Previous on the first page', () => {
    render(<Pager page={0} pageSize={25} total={60} onChange={() => {}} />)
    expect(screen.getByText('1–25 of 60')).toBeInTheDocument()
    expect(screen.getByText('Page 1 / 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
  })
  it('clamps the range on the last page and disables Next', () => {
    render(<Pager page={2} pageSize={25} total={60} onChange={() => {}} />)
    expect(screen.getByText('51–60 of 60')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })
  it('reports page changes', async () => {
    const onChange = vi.fn()
    render(<Pager page={1} pageSize={25} total={100} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(onChange.mock.calls).toEqual([[2], [0]])
  })
  it('says so when there are no results', () => {
    render(<Pager page={0} pageSize={25} total={0} onChange={() => {}} />)
    expect(screen.getByText('No results')).toBeInTheDocument()
  })
})

describe('StatCard', () => {
  it('renders the label and value', () => {
    render(<StatCard label="Expired" value={3} tone="danger" />)
    expect(screen.getByText('Expired')).toBeInTheDocument()
    expect(screen.getByText('3').className).toMatch(/text-danger/)
  })
})

describe('CertCard', () => {
  it('links to the certificate and shows expiry and owner', () => {
    render(<MemoryRouter><CertCard cert={cert({ owner: { name: 'payments' } })} /></MemoryRouter>)
    expect(screen.getByRole('link')).toHaveAttribute('href', '/certificates/c1')
    expect(screen.getByText(/Expires in (39|40)d/)).toBeInTheDocument()
    expect(screen.getByText('payments')).toBeInTheDocument()
  })
})

describe('ExpiryTimeline', () => {
  it('shows an empty state when no certificate has an expiry date', () => {
    render(<ExpiryTimeline buckets={[{ label: 'expired', total: 0 }]} />)
    expect(screen.getByText(/No certificates with an expiry date/)).toBeInTheDocument()
  })
  it('renders an accessible chart when there is data', () => {
    const buckets = [{ label: '0-7 d', low: 0, medium: 0, high: 1, critical: 1, unscored: 0, total: 2 }]
    render(<ExpiryTimeline buckets={buckets} />)
    expect(screen.getByRole('img', { name: /time to expiry/i })).toBeInTheDocument()
  })
})
