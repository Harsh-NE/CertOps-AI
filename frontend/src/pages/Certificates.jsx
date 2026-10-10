import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search } from 'lucide-react'
import api from '../api.js'
import { useAuth } from '../auth.jsx'
import Pager from '../components/Pager.jsx'
import RiskBadge from '../components/RiskBadge.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { daysLeft, errorMessage, expiryLabel, expiryTone, toQuery } from '../lib/format.js'

const PAGE_SIZE = 25
const STATUSES = ['valid', 'expiring_soon', 'expired', 'revoked', 'invalid_chain', 'hostname_mismatch', 'weak_crypto', 'unknown']
const EMPTY = { q: '', status: '', risk_tier: '', environment: '', source: '', expires_within_days: '' }

function Select({ label, value, onChange, options }) {
  return (
    <label className="text-xs text-muted flex flex-col gap-1">
      {label}
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className="bg-panel2 border border-border rounded-lg px-2 py-1.5 text-sm text-slate-200">
        <option value="">Any</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  )
}

export default function Certificates() {
  const { isOperator } = useAuth()
  const [filters, setFilters] = useState(EMPTY)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [scanTarget, setScanTarget] = useState('')
  const [scanMsg, setScanMsg] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [reload, setReload] = useState(0)

  // debounce the free-text search so every keystroke is not a request
  useEffect(() => {
    const t = setTimeout(() => { setFilters((f) => (f.q === search ? f : { ...f, q: search })); setPage(0) }, 300)
    return () => clearTimeout(t)
  }, [search])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get(`/certificates?${toQuery({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE })}`)
      .then((r) => { if (!cancelled) { setRows(r.data); setTotal(Number(r.headers['x-total-count'] ?? r.data.length)); setError(null) } })
      .catch((e) => !cancelled && setError(errorMessage(e, 'Could not load certificates')))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [filters, page, reload])

  const set = (key) => (value) => { setFilters((f) => ({ ...f, [key]: value })); setPage(0) }
  const active = Object.entries(filters).some(([k, v]) => k !== 'q' && v !== '') || filters.q !== ''

  async function scan(e) {
    e.preventDefault()
    const targets = scanTarget.split(',').map((t) => t.trim()).filter(Boolean)
    if (!targets.length) return
    setScanning(true); setScanMsg(null)
    try {
      const r = await api.post('/certificates/scan', { targets })
      setScanMsg({ ok: true, text: `Scanned ${r.data.length} endpoint(s).` })
      setScanTarget(''); setReload((n) => n + 1)
    } catch (err) {
      setScanMsg({ ok: false, text: errorMessage(err, 'Scan failed') })
    } finally { setScanning(false) }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Certificates</h1>
        <p className="text-muted text-sm mt-1">Soonest expiry first. Filters apply on the server.</p>
      </div>

      {isOperator && (
        <form onSubmit={scan} className="card flex flex-wrap gap-2 items-center">
          <input aria-label="Scan target" className="flex-1 min-w-[260px] bg-panel2 border border-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent"
            placeholder="host:port[#server_name], comma-separated" value={scanTarget} onChange={(e) => setScanTarget(e.target.value)} />
          <button disabled={scanning} className="bg-accent text-bg font-medium rounded-lg px-4 py-2 text-sm disabled:opacity-40">{scanning ? 'Scanning…' : 'Scan now'}</button>
          {scanMsg && <p role="status" className={`w-full text-sm ${scanMsg.ok ? 'text-accent2' : 'text-danger'}`}>{scanMsg.text}</p>}
        </form>
      )}

      <div className="card grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 items-end">
        <label className="text-xs text-muted flex flex-col gap-1 col-span-2">
          Search
          <span className="relative">
            <Search size={14} className="absolute left-2.5 top-2.5 text-muted" />
            <input aria-label="Search" className="w-full bg-panel2 border border-border rounded-lg pl-8 pr-3 py-1.5 text-sm text-slate-200 outline-none focus:border-accent"
              placeholder="name, issuer, host…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </span>
        </label>
        <Select label="Status" value={filters.status} onChange={set('status')} options={STATUSES.map((s) => [s, s.replace(/_/g, ' ')])} />
        <Select label="Risk" value={filters.risk_tier} onChange={set('risk_tier')} options={['low', 'medium', 'high', 'critical'].map((s) => [s, s])} />
        <Select label="Environment" value={filters.environment} onChange={set('environment')} options={['prod', 'staging', 'dev'].map((s) => [s, s])} />
        <Select label="Source" value={filters.source} onChange={set('source')}
          options={[['network_scan', 'network'], ['aws_acm', 'AWS ACM'], ['kubernetes_secret', 'Kubernetes'], ['azure_key_vault', 'Azure'], ['gcp_cert_manager', 'GCP']]} />
        <Select label="Expires within" value={filters.expires_within_days} onChange={set('expires_within_days')}
          options={[['7', '7 days'], ['14', '14 days'], ['30', '30 days'], ['90', '90 days']]} />
        {active && <button onClick={() => { setFilters(EMPTY); setSearch(''); setPage(0) }} className="text-xs text-accent text-left col-span-2 md:col-span-1">Clear filters</button>}
      </div>

      {error && <p role="alert" className="text-danger text-sm">{error}</p>}
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted border-b border-border">
              <th className="py-3 px-4">Certificate</th><th className="px-3">Env</th><th className="px-3">Status</th>
              <th className="px-3">Expiry</th><th className="px-3">Risk</th><th className="px-3">Source</th><th className="px-3">Owner</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const d = daysLeft(c.not_after)
              return (
                <tr key={c.id} className="border-b border-border/60 last:border-0 hover:bg-panel2/50">
                  <td className="py-2.5 px-4"><Link className="hover:text-accent font-medium" to={`/certificates/${c.id}`}>{c.common_name}</Link>
                    {c.expected_hostname && c.expected_hostname !== c.common_name && <span className="block text-xs text-muted">{c.expected_hostname}</span>}</td>
                  <td className="px-3 text-muted">{c.environment || '—'}</td>
                  <td className="px-3"><StatusBadge status={c.status} /></td>
                  <td className={`px-3 whitespace-nowrap ${expiryTone(d)}`}>{expiryLabel(d)}</td>
                  <td className="px-3"><RiskBadge tier={c.risk_tier} score={c.risk_score} /></td>
                  <td className="px-3 text-muted">{c.source}</td>
                  <td className="px-3 text-muted">{c.owner?.name || 'Unassigned'}</td>
                </tr>
              )
            })}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={7} className="py-8 text-center text-muted">{active ? 'No certificates match these filters.' : 'No certificates yet. Add scan targets on the Sources page.'}</td></tr>
            )}
          </tbody>
        </table>
        {loading && <p className="p-4 text-muted text-sm">Loading…</p>}
      </div>
      <Pager page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
    </div>
  )
}
