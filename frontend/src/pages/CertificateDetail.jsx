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
  const [validation, setValidation] = useState(null)
  const [risk, setRisk] = useState(null)
  const [plan, setPlan] = useState(null)

  async function load() {
    const res = await api.get(`/certificates/${id}`)
    setCert(res.data)
    const v = await api.get(`/certificates/${id}/validation`, { params: { limit: 1 } })
    setValidation(v.data[0] || null)
    const r = await api.get(`/risk/certificates/${id}`)
    setRisk(r.data)
  }
  useEffect(() => { load() }, [id])

  async function rescore() {
    setBusy(true)
    try {
      const res = await api.post(`/certificates/${id}/rescore`)
      setCert(res.data)
      await load()
      setMsg({ ok: true, text: 'Re-validated from stored data.' })
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

  // Renewal is two-step: a dry-run plan, then explicit human approval. Nothing real happens until "Approve & execute".
  async function planRenewal() {
    setBusy(true)
    try {
      const res = await api.post(`/renewals/${id}/plan`)
      setPlan(res.data)
      setMsg({ ok: true, text: 'Dry run complete. Review the plan below. Nothing has been changed.' })
    } catch (e) {
      setMsg({ ok: false, text: e.response?.data?.detail?.message || 'Could not create a plan.' })
    } finally { setBusy(false) }
  }

  async function approveAndExecute() {
    if (!window.confirm(`Approve and EXECUTE this renewal via ${plan.provider}? This issues a new certificate.`)) return
    setBusy(true)
    try {
      await api.post(`/renewals/plans/${plan.id}/approve`)
      const res = await api.post(`/renewals/plans/${plan.id}/execute`)
      setMsg({ ok: res.data.status !== 'failed', text: `Renewal ${res.data.status}${res.data.new_serial ? ` (new serial ${res.data.new_serial})` : ''}. ${res.data.notes || ''}` })
      setPlan(null)
      load()
    } catch (e) {
      setMsg({ ok: false, text: e.response?.data?.detail?.message || 'Renewal failed.' })
    } finally { setBusy(false) }
  }

  async function rejectPlan() {
    await api.post(`/renewals/plans/${plan.id}/reject`)
    setPlan(null)
    setMsg({ ok: true, text: 'Plan rejected.' })
  }

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
          <button onClick={planRenewal} disabled={busy} className="flex items-center gap-1.5 bg-accent text-bg font-medium rounded-lg px-3 py-2 text-sm hover:opacity-90 transition">
            <Zap size={14} /> Plan renewal via {cert.renewal_provider} (dry run)
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
        <Row label="30-day problem probability" value={risk ? `${(risk.probability * 100).toFixed(1)}%${risk.floor_reason ? ` (floor: ${risk.floor_reason})` : ''}` : null} />
        <Row label="Outage impact (rule-based)" value={cert.predicted_outage_impact} />
        <Row label="Model" value={risk ? `${risk.model_used}${risk.data_source === 'synthetic' ? ' (trained on synthetic data)' : ''}` : null} />
        {risk && risk.factors.length > 0 && (
          <p className="text-xs text-muted pt-2">Pushing risk up: {risk.factors.map((f) => `${f.feature}=${String(f.value)} (+${(f.effect * 100).toFixed(1)}pp)`).join(', ')}</p>
        )}
      </div>

      {plan && (
        <div className="card border-accent">
          <h2 className="text-sm font-medium mb-2">Renewal plan ({plan.provider}), expires {new Date(plan.expires_at).toLocaleTimeString()}</h2>
          <ol className="list-decimal ml-5 text-sm space-y-1">{plan.steps.map((st, i) => <li key={i}>{st}</li>)}</ol>
          {plan.warnings.map((w, i) => <p key={i} className="text-warn text-sm mt-2">Warning: {w}</p>)}
          {!plan.executable && <p className="text-danger text-sm mt-2">Cannot be executed: {plan.not_executable_reason}</p>}
          <div className="flex gap-2 mt-3">
            <button onClick={approveAndExecute} disabled={busy || !plan.executable} className="bg-accent text-bg rounded-lg px-3 py-2 text-sm disabled:opacity-40">Approve &amp; execute</button>
            <button onClick={rejectPlan} disabled={busy} className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm">Reject</button>
          </div>
        </div>
      )}

      <div className="card">
        <h2 className="text-sm font-medium mb-2">Validation {validation && <span className="text-muted">({validation.overall}, checked {new Date(validation.checked_at).toLocaleString()})</span>}</h2>
        {!validation && <p className="text-muted text-sm">Not validated yet.</p>}
        {validation && validation.findings.length === 0 && <p className="text-accent2 text-sm">All checks passed.</p>}
        {validation && validation.findings.map((f, i) => (
          <p key={i} className={`text-sm py-1 ${f.severity === 'error' ? 'text-danger' : f.severity === 'warning' ? 'text-warn' : 'text-muted'}`}>
            [{f.severity}] {f.message}
          </p>
        ))}
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
