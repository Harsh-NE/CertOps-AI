import React, { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, RefreshCw, Ticket, Zap } from 'lucide-react'
import api from '../api.js'
import { useAuth } from '../auth.jsx'
import RiskBadge from '../components/RiskBadge.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { daysLeft, errorMessage, expiryLabel, shortSerial } from '../lib/format.js'

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
  const { isOperator } = useAuth()
  const [cert, setCert] = useState(null)
  const [validation, setValidation] = useState(null)
  const [risk, setRisk] = useState(null)
  const [history, setHistory] = useState([])
  const [plan, setPlan] = useState(null)
  const [provider, setProvider] = useState('letsencrypt')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [loadError, setLoadError] = useState(null)

  const load = useCallback(async () => {
    try {
      const [c, v, r, h] = await Promise.all([
        api.get(`/certificates/${id}`),
        api.get(`/certificates/${id}/validation`, { params: { limit: 1 } }),
        api.get(`/risk/certificates/${id}`),
        api.get('/renewals', { params: { certificate_id: id, limit: 10 } }),
      ])
      setCert(c.data); setValidation(v.data[0] || null); setRisk(r.data); setHistory(h.data); setLoadError(null)
    } catch (e) {
      setLoadError(errorMessage(e, 'Could not load this certificate'))
    }
  }, [id])
  useEffect(() => { load() }, [load])

  async function run(action, success) {
    setBusy(true); setMsg(null)
    try { const text = await action(); setMsg({ ok: true, text: text || success }); await load() }
    catch (e) { setMsg({ ok: false, text: errorMessage(e) }) }
    finally { setBusy(false) }
  }

  const revalidate = () => run(async () => { await api.post(`/certificates/${id}/rescore`) }, 'Re-validated from stored data.')
  const ticket = () => run(async () => {
    const r = await api.post(`/renewals/${id}/create-ticket`)
    return r.data.jira_ticket_key ? `Jira ticket ${r.data.jira_ticket_key} created.` : 'Renewal task recorded (Jira is not configured).'
  })
  const toggleAuto = () => run(async () => { await api.post(`/certificates/${id}/toggle-auto-renew`, null, { params: { provider } }) },
    cert?.auto_renew_enabled ? 'Auto-renew disabled.' : `Renewal provider set to ${provider}.`)

  // Renewal is two-step: a dry-run plan, then explicit human approval. Nothing real happens until "Approve & execute".
  const planRenewal = () => run(async () => {
    const r = await api.post(`/renewals/${id}/plan`)
    setPlan(r.data)
    return 'Dry run complete. Review the plan below; nothing has been changed.'
  })
  async function approveAndExecute() {
    if (!window.confirm(`Approve and EXECUTE this renewal via ${plan.provider}? This issues a new certificate.`)) return
    await run(async () => {
      await api.post(`/renewals/plans/${plan.id}/approve`)
      const r = await api.post(`/renewals/plans/${plan.id}/execute`)
      setPlan(null)
      return `Renewal ${r.data.status}${r.data.new_serial ? ` (new serial ${shortSerial(r.data.new_serial)})` : ''}.`
    })
  }
  const rejectPlan = () => run(async () => { await api.post(`/renewals/plans/${plan.id}/reject`); setPlan(null) }, 'Plan rejected.')

  if (loadError) return <p role="alert" className="text-danger">{loadError} <Link className="underline" to="/certificates">Back to certificates</Link></p>
  if (!cert) return <p className="text-muted">Loading…</p>
  const d = daysLeft(cert.not_after)

  return (
    <div className="space-y-6 max-w-3xl">
      <Link to="/certificates" className="flex items-center gap-1.5 text-sm text-muted hover:text-accent w-fit"><ArrowLeft size={16} /> Back to certificates</Link>

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{cert.common_name}</h1>
          <p className="text-muted text-sm mt-1">{cert.environment || 'no environment'} · {cert.source} · {cert.source_ref}</p>
          <p className="text-sm mt-2 flex items-center gap-2"><StatusBadge status={cert.status} /> <span className="text-muted">{expiryLabel(d)}</span></p>
        </div>
        <RiskBadge tier={cert.risk_tier} score={cert.risk_score} />
      </div>

      {isOperator && (
        <div className="flex gap-2 flex-wrap items-center">
          <button onClick={revalidate} disabled={busy} className="flex items-center gap-1.5 bg-panel2 border border-border rounded-lg px-3 py-2 text-sm hover:border-accent transition"><RefreshCw size={14} /> Re-validate</button>
          <button onClick={ticket} disabled={busy} className="flex items-center gap-1.5 bg-panel2 border border-border rounded-lg px-3 py-2 text-sm hover:border-accent transition"><Ticket size={14} /> Create renewal ticket</button>
          {cert.renewal_provider ? (
            <button onClick={planRenewal} disabled={busy} className="flex items-center gap-1.5 bg-accent text-bg font-medium rounded-lg px-3 py-2 text-sm hover:opacity-90 transition"><Zap size={14} /> Plan renewal via {cert.renewal_provider} (dry run)</button>
          ) : (
            <span className="text-xs text-muted">Set a renewal provider below to plan a renewal.</span>
          )}
        </div>
      )}
      {msg && <p role="status" className={`text-sm ${msg.ok ? 'text-accent2' : 'text-danger'}`}>{msg.text}</p>}

      {plan && (
        <section className="card border-accent" aria-label="Renewal plan">
          <h2 className="text-sm font-medium mb-2">Renewal plan ({plan.provider}); expires {new Date(plan.expires_at).toLocaleTimeString()}</h2>
          <ol className="list-decimal ml-5 text-sm space-y-1">{plan.steps.map((st, i) => <li key={i}>{st}</li>)}</ol>
          {plan.warnings.map((w, i) => <p key={i} className="text-warn text-sm mt-2">Warning: {w}</p>)}
          {!plan.executable && <p className="text-danger text-sm mt-2">Cannot be executed: {plan.not_executable_reason}</p>}
          <div className="flex gap-2 mt-3">
            <button onClick={approveAndExecute} disabled={busy || !plan.executable} className="bg-accent text-bg rounded-lg px-3 py-2 text-sm disabled:opacity-40">Approve &amp; execute</button>
            <button onClick={rejectPlan} disabled={busy} className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm">Reject</button>
          </div>
        </section>
      )}

      <section className="card" aria-label="Validation">
        <h2 className="text-sm font-medium mb-2">Validation {validation && <span className="text-muted font-normal">({validation.overall}, checked {new Date(validation.checked_at).toLocaleString()})</span>}</h2>
        {!validation && <p className="text-muted text-sm">Not validated yet.</p>}
        {validation && validation.findings.length === 0 && <p className="text-accent2 text-sm">All checks passed.</p>}
        {validation?.findings.map((f, i) => (
          <p key={i} className={`text-sm py-1 ${f.severity === 'error' ? 'text-danger' : f.severity === 'warning' ? 'text-warn' : 'text-muted'}`}>[{f.severity}] {f.message}</p>
        ))}
      </section>

      <section className="card" aria-label="Risk">
        <h2 className="text-sm font-medium mb-2">Risk</h2>
        <Row label="Risk score" value={cert.risk_score} />
        <Row label="30-day problem probability" value={risk ? `${(risk.probability * 100).toFixed(1)}%${risk.floor_reason ? ` (floor: ${risk.floor_reason})` : ''}` : null} />
        <Row label="Outage impact (rule-based)" value={cert.predicted_outage_impact} />
        <Row label="Model" value={risk ? `${risk.model_used}${risk.data_source === 'synthetic' ? ' (trained on synthetic data)' : ''}` : null} />
        {risk?.factors.length > 0 && (
          <p className="text-xs text-muted pt-2">Pushing risk up: {risk.factors.map((f) => `${f.feature}=${String(f.value)} (+${(f.effect * 100).toFixed(1)}pp)`).join(', ')}</p>
        )}
      </section>

      <section className="card" aria-label="Certificate details">
        <h2 className="text-sm font-medium mb-2">Certificate</h2>
        <Row label="Issuer" value={cert.issuer} />
        <Row label="Subject" value={cert.subject} />
        <Row label="Valid from" value={cert.not_before && new Date(cert.not_before).toLocaleString()} />
        <Row label="Valid until" value={cert.not_after && new Date(cert.not_after).toLocaleString()} />
        <Row label="Key" value={cert.key_size ? `${cert.key_algorithm || ''} ${cert.key_size} bits` : null} />
        <Row label="Signature algorithm" value={cert.signature_algorithm} />
        <Row label="Chain valid" value={cert.chain_valid === null ? 'not determined' : String(cert.chain_valid)} />
        <Row label="Revoked" value={cert.revoked === null ? 'not determined' : String(cert.revoked)} />
      </section>

      <section className="card" aria-label="Ownership and automation">
        <h2 className="text-sm font-medium mb-2">Ownership &amp; automation</h2>
        <Row label="Owner" value={cert.owner?.name} />
        <Row label="Auto-renew" value={cert.auto_renew_enabled ? `on (${cert.renewal_provider})` : 'off'} />
        {isOperator && (
          <div className="flex items-center gap-2 pt-3 text-sm">
            <select aria-label="Renewal provider" value={provider} onChange={(e) => setProvider(e.target.value)} className="bg-panel2 border border-border rounded-lg px-2 py-1.5">
              <option value="letsencrypt">letsencrypt (ACME)</option><option value="aws_acm">aws_acm</option>
            </select>
            <button onClick={toggleAuto} disabled={busy} className="bg-panel2 border border-border rounded-lg px-3 py-1.5 hover:border-accent">{cert.auto_renew_enabled ? 'Disable auto-renew' : 'Enable renewal provider'}</button>
          </div>
        )}
      </section>

      <section className="card" aria-label="Renewal history">
        <h2 className="text-sm font-medium mb-2">Renewal history</h2>
        {history.length === 0 ? <p className="text-muted text-sm">No renewal activity yet.</p> : history.map((t) => (
          <p key={t.id} className="text-sm py-1 border-b border-border/60 last:border-0">
            <span className="text-muted">{new Date(t.created_at).toLocaleString()}</span> · <span className="font-medium">{t.status.replace('_', ' ')}</span> · {t.provider || '—'} · <span className="text-muted">{t.triggered_by}</span>
          </p>
        ))}
        <Link to="/renewals" className="text-xs text-accent mt-2 inline-block">All renewals</Link>
      </section>
    </div>
  )
}
