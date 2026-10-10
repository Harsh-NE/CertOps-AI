import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api.js'
import { useAuth } from '../auth.jsx'
import { errorMessage, shortSerial, toQuery } from '../lib/format.js'

const STATUS_COLOR = {
  pending: 'text-muted', jira_created: 'text-accent', in_progress: 'text-warn', auto_renewed: 'text-accent2',
  completed: 'text-accent2', failed: 'text-danger', dry_run: 'text-muted', skipped: 'text-warn',
}
const OPEN = new Set(['pending_approval', 'approved'])

export default function Renewals() {
  const { isOperator } = useAuth()
  const [tasks, setTasks] = useState([])
  const [plans, setPlans] = useState([])
  const [status, setStatus] = useState('')
  const [showDry, setShowDry] = useState(true)
  const [loading, setLoading] = useState(true)
  const [note, setNote] = useState(null)

  function load() {
    setLoading(true)
    Promise.all([
      api.get(`/renewals?${toQuery({ status, include_dry_runs: showDry })}`),
      api.get('/renewals/plans'),
    ]).then(([t, p]) => { setTasks(t.data); setPlans(p.data) })
      .catch((e) => setNote({ ok: false, text: errorMessage(e) }))
      .finally(() => setLoading(false))
  }
  useEffect(load, [status, showDry])

  async function decide(plan, action) {
    if (action === 'execute' && !window.confirm(`Approve and EXECUTE the renewal plan for ${plan.provider}? This issues a new certificate.`)) return
    try {
      if (action === 'execute') {
        await api.post(`/renewals/plans/${plan.id}/approve`)
        await api.post(`/renewals/plans/${plan.id}/execute`)
        setNote({ ok: true, text: 'Plan approved and executed.' })
      } else {
        await api.post(`/renewals/plans/${plan.id}/reject`)
        setNote({ ok: true, text: 'Plan rejected.' })
      }
      load()
    } catch (e) {
      setNote({ ok: false, text: errorMessage(e, 'Action failed') })
      load()
    }
  }

  const open = plans.filter((p) => OPEN.has(p.status))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Renewals</h1>
        <p className="text-muted text-sm mt-1">Every plan and attempt (by users, the agent, or the scheduler) is recorded here. Renewals run only after a human approves a plan.</p>
      </div>
      {note && <p role="status" className={`text-sm ${note.ok ? 'text-accent2' : 'text-danger'}`}>{note.text}</p>}

      <section className="card" aria-labelledby="plans-h">
        <h2 id="plans-h" className="text-sm font-medium mb-2">Plans awaiting a human decision ({open.length})</h2>
        {open.length === 0 && <p className="text-muted text-sm">None. Ask the agent or use “Plan renewal” on a certificate.</p>}
        {open.map((p) => (
          <div key={p.id} className="border-b border-border/60 last:border-0 py-3 text-sm">
            <p><Link className="font-medium hover:text-accent" to={`/certificates/${p.certificate_id}`}>{p.provider}</Link>
              <span className="text-muted"> · by {p.created_by} · {p.status.replace('_', ' ')} · expires {new Date(p.expires_at).toLocaleTimeString()}</span></p>
            <ol className="list-decimal ml-5 text-muted">{p.steps.map((st, i) => <li key={i}>{st}</li>)}</ol>
            {p.warnings.map((w, i) => <p key={i} className="text-warn">Warning: {w}</p>)}
            {!p.executable && <p className="text-danger">Cannot be executed: {p.not_executable_reason}</p>}
            {isOperator && (
              <div className="flex gap-2 mt-2">
                <button disabled={!p.executable} onClick={() => decide(p, 'execute')} className="bg-accent text-bg rounded-lg px-3 py-1.5 disabled:opacity-40">Approve &amp; execute</button>
                <button onClick={() => decide(p, 'reject')} className="bg-panel2 border border-border rounded-lg px-3 py-1.5">Reject</button>
              </div>
            )}
          </div>
        ))}
      </section>

      <section className="card" aria-labelledby="hist-h">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
          <h2 id="hist-h" className="text-sm font-medium">History</h2>
          <div className="flex items-center gap-4 text-sm text-muted">
            <label className="flex items-center gap-2">Status
              <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className="bg-panel2 border border-border rounded-lg px-2 py-1 text-slate-200">
                <option value="">All</option>
                {['auto_renewed', 'failed', 'skipped', 'dry_run', 'jira_created', 'pending'].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={showDry} onChange={(e) => setShowDry(e.target.checked)} /> Show dry runs</label>
          </div>
        </div>
        {loading ? <p className="text-muted text-sm">Loading…</p> : tasks.length === 0 ? <p className="text-muted text-sm">No renewal activity matches.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-muted border-b border-border">
                <th className="py-2 pr-3">When</th><th className="pr-3">Certificate</th><th className="pr-3">Provider</th><th className="pr-3">Status</th>
                <th className="pr-3">Serial</th><th className="pr-3">By</th><th>Notes</th></tr></thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id} className="border-b border-border/60 last:border-0 align-top">
                    <td className="py-2 pr-3 whitespace-nowrap text-muted">{new Date(t.created_at).toLocaleString()}</td>
                    <td className="pr-3"><Link className="hover:text-accent" to={`/certificates/${t.certificate_id}`}>{t.certificate_name || t.certificate_id.slice(0, 8)}</Link></td>
                    <td className="pr-3">{t.provider || '—'}</td>
                    <td className={`pr-3 font-medium ${STATUS_COLOR[t.status] || ''}`}>{t.status.replace('_', ' ')}</td>
                    <td className="pr-3 text-muted whitespace-nowrap">{t.new_serial ? `${shortSerial(t.previous_serial)} → ${shortSerial(t.new_serial)}` : '—'}</td>
                    <td className="pr-3 text-muted">{t.triggered_by}</td>
                    <td className="text-muted max-w-xs truncate" title={t.notes || ''}>{t.notes || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
