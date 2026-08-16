import React, { useEffect, useState } from 'react'
import api from '../api.js'

const STATUS_COLOR = {
  pending: 'text-muted',
  jira_created: 'text-accent',
  in_progress: 'text-warn',
  auto_renewed: 'text-accent2',
  completed: 'text-accent2',
  failed: 'text-danger',
}

export default function Renewals() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/renewals').then((r) => setTasks(r.data)).finally(() => setLoading(false))
  }, [])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Renewal Tasks</h1>
        <p className="text-muted text-sm mt-1">Every renewal attempt — scheduler, agent, or manual — is logged here.</p>
      </div>

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
