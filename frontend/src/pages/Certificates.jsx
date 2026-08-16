import React, { useEffect, useState } from 'react'
import api from '../api.js'
import CertCard from '../components/CertCard.jsx'
import { Search, ScanLine } from 'lucide-react'

export default function Certificates() {
  const [certs, setCerts] = useState([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [scanTarget, setScanTarget] = useState('')
  const [scanning, setScanning] = useState(false)
  const [scanMsg, setScanMsg] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const res = await api.get('/certificates')
      setCerts(res.data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function runScan(e) {
    e.preventDefault()
    if (!scanTarget.trim()) return
    setScanning(true)
    setScanMsg(null)
    try {
      const targets = scanTarget.split(',').map((t) => t.trim()).filter(Boolean)
      await api.post('/certificates/scan', { targets })
      setScanMsg({ ok: true, text: `Scanned ${targets.length} target(s).` })
      setScanTarget('')
      load()
    } catch (err) {
      setScanMsg({ ok: false, text: err.response?.data?.detail ? JSON.stringify(err.response.data.detail) : 'Scan failed' })
    } finally {
      setScanning(false)
    }
  }

  const filtered = certs.filter((c) =>
    c.common_name?.toLowerCase().includes(filter.toLowerCase()) ||
    c.owner?.name?.toLowerCase().includes(filter.toLowerCase())
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-xl font-semibold">Certificates</h1>
          <p className="text-muted text-sm mt-1">{certs.length} discovered</p>
        </div>
        <form onSubmit={runScan} className="flex items-center gap-2">
          <input
            className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm w-72 outline-none focus:border-accent"
            placeholder="host:port, e.g. app1.lab.local:8443"
            value={scanTarget}
            onChange={(e) => setScanTarget(e.target.value)}
          />
          <button
            disabled={scanning}
            className="flex items-center gap-1.5 bg-accent text-bg font-medium rounded-lg px-3 py-2 text-sm hover:opacity-90 transition"
          >
            <ScanLine size={16} /> {scanning ? 'Scanning…' : 'Scan'}
          </button>
        </form>
      </div>

      {scanMsg && (
        <p className={`text-sm ${scanMsg.ok ? 'text-accent2' : 'text-danger'}`}>{scanMsg.text}</p>
      )}

      <div className="relative max-w-sm">
        <Search size={16} className="absolute left-3 top-2.5 text-muted" />
        <input
          className="w-full bg-panel2 border border-border rounded-lg pl-9 pr-3 py-2 text-sm outline-none focus:border-accent"
          placeholder="Filter by name or owner…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {loading ? (
        <p className="text-muted">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-muted text-sm">No certificates match. Try scanning a target above, or run the lab seed script.</p>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((c) => <CertCard key={c.id} cert={c} />)}
        </div>
      )}
    </div>
  )
}
