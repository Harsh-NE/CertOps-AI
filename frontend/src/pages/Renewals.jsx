import React, { useEffect, useState } from 'react'
import api from '../api.js'

const STATUS_COLOR = {
  pending: 'text-muted',
  jira_created: 'text-accent',
  in_progress: 'text-warn',
  auto_renewed: 'text-accent2',
  completed: 'text-accent2',
  failed: 'text-danger',
  dry_run: 'text-muted',
  skipped: 'text-warn',
}

export default function Renewals() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [plans, setPlans] = useState([])
  const [note, setNote] = useState(null)

  function load() {
    api.get('/renewals').then((r) => setTasks(r.data)).finally(() => setLoading(false))
    api.get('/renewals/plans').then((r) => setPlans(r.data))
  }
  useEffect(() => { load() }, [])

  async function act(plan, action) {
    if (action === 'execute' && !window.confirm(`Approve and EXECUTE the renewal plan for ${plan.provider}?`)) return
    try {
      if (action === 'execute') await api.post(`/renewals/plans/${plan.id}/approve`)
      await api.post(`/renewals/plans/${plan.id}/${action}`)
      setNote({ ok: true, text: action === 'execute' ? 'Plan approved and executed.' : 'Plan rejected.' })
      load()
    } catch (e) {
      setNote({ ok: false, text: e.response?.data?.detail?.message || 'Action failed.' })
    }
  }


  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Renewal Tasks</h1>
        <p className="text-muted text-sm mt-1">Every renewal attempt — scheduler, agent, or manual — is logged here.</p>
      </div>

      {note && <p className={`text-sm ${note.ok ? 'text-accent2' : 'text-danger'}`}>{note.text}</p>}
      {plans.filter((p) => p.status === 'pending_approval' || p.status === 'approved').length > 0 && (
        <div className="card">
          <h2 className="text-sm font-medium mb-2">Plans awaiting a human decision</h2>
          {plans.filter((p) => p.status === 'pending_approval' || p.status === 'approved').map((p) => (
            <div key={p.id} className="border-b border-border/60 py-2 text-sm">
              <p><span className="font-medium">{p.provider}</span> <span className="text-muted">by {p.created_by}, {p.status}</span></p>
              <ol className="list-decimal ml-5 text-muted">{p.steps.map((st, i) => <li key={i}>{st}</li>)}</ol>
              {!p.executable && <p className="text-danger">Cannot be executed: {p.not_executable_reason}</p>}
              <div className="flex gap-2 mt-2">
                <button disabled={!p.executable} onClick={() => act(p, 'execute')} className="bg-accent text-bg rounded-lg px-3 py-1.5 disabled:opacity-40">Approve &amp; execute</button>
                <button onClick={() => act(p, 'reject')} className="bg-panel2 border border-border rounded-lg px-3 py-1.5">Reject</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <p className="text-muted">Loading…</p>
      ) : tasks.length === 0 ? (
        <p className="text-muted text-sm">No renewal tasks yet.</p>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Jira</th>
                <th className="py-2 pr-4">Triggered by</th>
                <th className="py-2 pr-4">Notes</th>
                <th className="py-2 pr-4">Created</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id} className="border-b border-border/60 last:border-0">
                  <td className={`py-2 pr-4 font-medium ${STATUS_COLOR[t.status] || ''}`}>{t.status}</td>
                  <td className="py-2 pr-4">{t.jira_ticket_key || '—'}</td>
                  <td className="py-2 pr-4">{t.triggered_by}</td>
                  <td className="py-2 pr-4 text-muted max-w-xs truncate">{t.notes || '—'}</td>
                  <td className="py-2 pr-4 text-muted">{new Date(t.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
