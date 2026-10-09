import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import api from '../api.js'
import RiskBadge from '../components/RiskBadge.jsx'
import { ArrowLeft, RefreshCw, Ticket, Zap } from 'lucide-react'

function Row({ label, value }) {
  return (
    <div className="flex justify-between py-2 border-b border-border/60 text-sm">
      <span className="text-muted">{label}</span>
      <span className="text-slate-200 text-right max-w-[60%] break-words">{value ?? '—'}</span>
    </div>
  )
}

export default function CertificateDetail() {
  const { id } = useParams()
  const [cert, setCert] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  async function load() {
    const res = await api.get(`/certificates/${id}`)
    setCert(res.data)
  }
  useEffect(() => { load() }, [id])

  async function rescore() {
    setBusy(true)
    try {
      const res = await api.post(`/certificates/${id}/rescore`)
      setCert(res.data)
      setMsg({ ok: true, text: 'Re-scored.' })
    } finally { setBusy(false) }
  }

  async function createTicket() {
    setBusy(true)
    try {
      const res = await api.post(`/renewals/${id}/create-ticket`)
      setMsg({ ok: true, text: res.data.jira_ticket_key ? `Jira ticket ${res.data.jira_ticket_key} created.` : 'Renewal task created (Jira not configured).' })
    } catch (e) {
      setMsg({ ok: false, text: 'Failed to create ticket.' })
    } finally { setBusy(false) }
  }

  // Dry-run first (changes nothing); a real renewal needs an explicit confirmation.
  async function renewNow() {
    setBusy(true)
    try {
      const dry = await api.post(`/renewals/${id}/renew-now`, null, { params: { dry_run: true } })
      const proceed = window.confirm(`${dry.data.notes}

Perform the REAL renewal now?`)
      if (!proceed) {
        setMsg({ ok: true, text: 'Dry run recorded. No changes made.' })
        return
      }
      const res = await api.post(`/renewals/${id}/renew-now`, null, { params: { dry_run: false, confirm: true } })
      setMsg({ ok: res.data.status !== 'failed', text: `Renewal attempt: ${res.data.status}` })
      load()
    } catch (e) {
      setMsg({ ok: false, text: e.response?.data?.detail || 'Renewal failed' })
    } finally { setBusy(false) }
  }


  if (!cert) return <p className="text-muted">Loading…</p>

  return (
    <div className="space-y-6 max-w-3xl">
      <Link to="/certificates" className="flex items-center gap-1.5 text-sm text-muted hover:text-accent w-fit">
        <ArrowLeft size={16} /> Back to certificates
      </Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">{cert.common_name}</h1>
          <p className="text-muted text-sm mt-1">{cert.source} · {cert.source_ref}</p>
        </div>
        <RiskBadge tier={cert.risk_tier} />
      </div>

      <div className="flex gap-2 flex-wrap">
        <button onClick={rescore} disabled={busy} className="flex items-center gap-1.5 bg-panel2 border border-border rounded-lg px-3 py-2 text-sm hover:border-accent transition">
          <RefreshCw size={14} /> Re-score
        </button>
        <button onClick={createTicket} disabled={busy} className="flex items-center gap-1.5 bg-panel2 border border-border rounded-lg px-3 py-2 text-sm hover:border-accent transition">
          <Ticket size={14} /> Create renewal ticket
        </button>
        {cert.renewal_provider && (
          <button onClick={renewNow} disabled={busy} className="flex items-center gap-1.5 bg-accent text-bg font-medium rounded-lg px-3 py-2 text-sm hover:opacity-90 transition">
            <Zap size={14} /> Renew via {cert.renewal_provider} (dry run first)
          </button>
        )}
      </div>

      {msg && <p className={`text-sm ${msg.ok ? 'text-accent2' : 'text-danger'}`}>{msg.text}</p>}

      <div className="card">
        <h2 className="text-sm font-medium mb-2">Certificate details</h2>
        <Row label="Status" value={cert.status} />
        <Row label="Issuer" value={cert.issuer} />
        <Row label="Subject" value={cert.subject} />
        <Row label="Not before" value={cert.not_before} />
        <Row label="Not after" value={cert.not_after} />
        <Row label="Signature algorithm" value={cert.signature_algorithm} />
        <Row label="Key size" value={cert.key_size ? `${cert.key_size} bits` : null} />
        <Row label="Chain valid" value={cert.chain_valid === null ? null : String(cert.chain_valid)} />
        <Row label="Revoked" value={cert.revoked === null ? null : String(cert.revoked)} />
      </div>

      <div className="card">
        <h2 className="text-sm font-medium mb-2">Risk &amp; prediction</h2>
        <Row label="Risk score" value={cert.risk_score} />
        <Row label="Risk tier" value={cert.risk_tier} />
        <Row label="Predicted renewal delay" value={cert.predicted_renewal_delay_days ? `${cert.predicted_renewal_delay_days} days` : null} />
        <Row label="Predicted outage impact" value={cert.predicted_outage_impact} />
      </div>

      <div className="card">
        <h2 className="text-sm font-medium mb-2">Ownership &amp; automation</h2>
        <Row label="Owner" value={cert.owner?.name} />
        <Row label="Team" value={cert.owner?.team} />
        <Row label="Ownership source" value={cert.owner?.source} />
        <Row label="Auto-renew enabled" value={String(cert.auto_renew_enabled)} />
        <Row label="Renewal provider" value={cert.renewal_provider} />
      </div>
    </div>
  )
}
