import React, { useEffect, useState } from 'react'
import api from '../api.js'
import StatCard from '../components/StatCard.jsx'
import CertCard from '../components/CertCard.jsx'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts'
import { ShieldAlert, Clock, XCircle, GitBranch, RefreshCw, Activity } from 'lucide-react'

const COLORS = { low: '#7cf5c0', medium: '#ffb454', high: '#ff5c7a', critical: '#c23b57' }

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [certs, setCerts] = useState([])
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      const [statsRes, certsRes] = await Promise.all([
        api.get('/risk/dashboard'),
        api.get('/certificates'),
      ])
      setStats(statsRes.data)
      setCerts(certsRes.data)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const tierCounts = certs.reduce((acc, c) => {
    const t = c.risk_tier || 'unscored'
    acc[t] = (acc[t] || 0) + 1
    return acc
  }, {})
  const pieData = Object.entries(tierCounts)
    .filter(([k]) => k !== 'unscored')
    .map(([k, v]) => ({ name: k, value: v }))

  const urgent = [...certs]
    .filter((c) => c.not_after)
    .sort((a, b) => new Date(a.not_after) - new Date(b.not_after))
    .slice(0, 6)

  if (loading) return <p className="text-muted">Loading dashboard…</p>

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-muted text-sm mt-1">Fleet-wide certificate health, at a glance.</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Certificates" value={stats.total_certificates} icon={Activity} />
        <StatCard label="Expiring ≤ 7d" value={stats.expiring_7d} tone="danger" icon={Clock} />
        <StatCard label="Expired" value={stats.expired} tone="danger" icon={XCircle} />
        <StatCard label="Invalid Chain" value={stats.invalid_chain} tone="warn" icon={GitBranch} />
        <StatCard label="High / Critical Risk" value={stats.high_risk} tone="warn" icon={ShieldAlert} />
        <StatCard label="Auto-Renew Enabled" value={stats.auto_renew_enabled} tone="good" icon={RefreshCw} />
        <StatCard label="Security Score" value={`${stats.security_score}/100`} tone={stats.security_score > 80 ? 'good' : stats.security_score > 50 ? 'warn' : 'danger'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="card lg:col-span-1">
          <h2 className="text-sm font-medium mb-4">Risk Distribution</h2>
          {pieData.length === 0 ? (
            <p className="text-muted text-sm">No scored certificates yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={3}>
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={COLORS[entry.name] || '#4f9dff'} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: '#121821', border: '1px solid #243040' }} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="lg:col-span-2">
          <h2 className="text-sm font-medium mb-4">Nearest to expiry</h2>
          <div className="grid sm:grid-cols-2 gap-4">
            {urgent.length === 0 && <p className="text-muted text-sm">No certificates discovered yet — scan a target to get started.</p>}
            {urgent.map((c) => <CertCard key={c.id} cert={c} />)}
          </div>
        </div>
      </div>
    </div>
  )
}
