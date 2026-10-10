import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Clock, FileCheck, GitBranch, ShieldAlert, XCircle } from 'lucide-react'
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import api from '../api.js'
import ExpiryTimeline, { TIER_COLORS } from '../components/ExpiryTimeline.jsx'
import RiskBadge from '../components/RiskBadge.jsx'
import StatCard from '../components/StatCard.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { daysLeft, errorMessage, expiryLabel, expiryTone } from '../lib/format.js'

const NEEDS_ATTENTION = new Set(['critical', 'high'])
const BAD_STATUS = new Set(['expired', 'revoked', 'invalid_chain'])

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [timeline, setTimeline] = useState([])
  const [certs, setCerts] = useState([])
  const [jobs, setJobs] = useState([])
  const [pending, setPending] = useState(0)
  const [env, setEnv] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const envParam = env ? { environment: env } : {}
    Promise.all([
      api.get('/risk/dashboard'),
      api.get('/risk/expiry-timeline', { params: envParam }),
      api.get('/certificates', { params: { ...envParam, limit: 200 } }),
      api.get('/jobs', { params: { limit: 5 } }),
      api.get('/renewals/plans', { params: { status: 'pending_approval' } }),
    ])
      .then(([s, t, c, j, p]) => {
        if (cancelled) return
        setStats(s.data); setTimeline(t.data.buckets); setCerts(c.data); setJobs(j.data); setPending(p.data.length)
        setError(null)
      })
      .catch((e) => !cancelled && setError(errorMessage(e, 'Could not load the dashboard')))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [env])

  if (error) return <p role="alert" className="text-danger">{error}</p>
  if (loading && !stats) return <p className="text-muted">Loading dashboard…</p>

  const tierCounts = certs.reduce((acc, c) => { const t = c.risk_tier || 'unscored'; acc[t] = (acc[t] || 0) + 1; return acc }, {})
  const pie = Object.entries(tierCounts).map(([name, value]) => ({ name, value }))
  const attention = certs
    .filter((c) => NEEDS_ATTENTION.has(c.risk_tier) || BAD_STATUS.has(c.status) || (daysLeft(c.not_after) ?? 999) <= 14)
    .slice(0, 8)

  return (
    <div className="space-y-8">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-muted text-sm mt-1">Fleet-wide certificate health.</p>
        </div>
        <label className="text-sm text-muted flex items-center gap-2">
          Environment
          <select aria-label="Environment" value={env} onChange={(e) => setEnv(e.target.value)} className="bg-panel2 border border-border rounded-lg px-2 py-1.5 text-slate-200">
            <option value="">All</option><option value="prod">prod</option><option value="staging">staging</option><option value="dev">dev</option>
          </select>
        </label>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <StatCard label="Certificates" value={stats.total_certificates} icon={Activity} />
        <StatCard label="Expiring ≤ 7d" value={stats.expiring_7d} tone={stats.expiring_7d ? 'danger' : 'default'} icon={Clock} />
        <StatCard label="Expired" value={stats.expired} tone={stats.expired ? 'danger' : 'default'} icon={XCircle} />
        <StatCard label="Invalid chain" value={stats.invalid_chain} tone={stats.invalid_chain ? 'warn' : 'default'} icon={GitBranch} />
        <StatCard label="High / critical" value={stats.high_risk} tone={stats.high_risk ? 'warn' : 'default'} icon={ShieldAlert} />
        <StatCard label="Plans awaiting approval" value={pending} tone={pending ? 'warn' : 'default'} icon={FileCheck} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="card lg:col-span-2">
          <h2 className="text-sm font-medium mb-3">Expiry timeline <span className="text-muted font-normal">(stacked by risk tier)</span></h2>
          <ExpiryTimeline buckets={timeline} />
        </div>
        <div className="card">
          <h2 className="text-sm font-medium mb-3">Risk distribution</h2>
          {pie.length === 0 ? <p className="text-muted text-sm">No certificates yet.</p> : (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={pie} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={3}>
                  {pie.map((d) => <Cell key={d.name} fill={TIER_COLORS[d.name] || '#4f9dff'} />)}
                </Pie>
                <Tooltip contentStyle={{ background: '#121821', border: '1px solid #243040' }} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
          <p className="text-xs text-muted mt-2">Risk tiers come from a model trained on synthetic data plus explicit rules for failing certificates.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="card lg:col-span-2">
          <h2 className="text-sm font-medium mb-3">Needs attention</h2>
          {attention.length === 0 ? <p className="text-muted text-sm">Nothing urgent.</p> : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-muted border-b border-border"><th className="py-2 pr-3">Certificate</th><th className="pr-3">Status</th><th className="pr-3">Expiry</th><th>Risk</th></tr></thead>
              <tbody>
                {attention.map((c) => {
                  const d = daysLeft(c.not_after)
                  return (
                    <tr key={c.id} className="border-b border-border/60 last:border-0">
                      <td className="py-2 pr-3"><Link className="hover:text-accent" to={`/certificates/${c.id}`}>{c.common_name}</Link><span className="block text-xs text-muted">{c.environment || 'no env'}</span></td>
                      <td className="pr-3"><StatusBadge status={c.status} /></td>
                      <td className={`pr-3 ${expiryTone(d)}`}>{expiryLabel(d)}</td>
                      <td><RiskBadge tier={c.risk_tier} score={c.risk_score} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h2 className="text-sm font-medium mb-3">Discovery health</h2>
          {jobs.length === 0 ? <p className="text-muted text-sm">No discovery runs yet. Add targets on the Sources page.</p> : (
            <ul className="space-y-2 text-sm">
              {jobs.map((j) => (
                <li key={j.id} className="flex items-center justify-between">
                  <span>{j.provider}<span className="block text-xs text-muted">{new Date(j.started_at).toLocaleString()} · {j.discovered} found</span></span>
                  <span className={j.status === 'succeeded' ? 'text-accent2' : j.status === 'skipped' ? 'text-muted' : 'text-danger'}>{j.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
