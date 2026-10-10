import React, { useEffect, useState } from 'react'
import { Cloud, Trash2 } from 'lucide-react'
import api from '../api.js'
import { useAuth } from '../auth.jsx'
import { errorMessage } from '../lib/format.js'

const PROVIDERS = [
  ['network_scan', 'Network scan targets'], ['aws_acm', 'AWS Certificate Manager'], ['azure_key_vault', 'Azure Key Vault'],
  ['gcp_cert_manager', 'Google Certificate Manager'], ['kubernetes_secret', 'Kubernetes Secrets'],
]
const EMPTY = { host: '', port: 443, server_name: '', environment: '' }
const JOB_TONE = { succeeded: 'text-accent2', partial: 'text-warn', failed: 'text-danger', skipped: 'text-muted', running: 'text-accent' }

export default function Sources() {
  const { isOperator } = useAuth()
  const [targets, setTargets] = useState([])
  const [jobs, setJobs] = useState([])
  const [form, setForm] = useState(EMPTY)
  const [selected, setSelected] = useState(['network_scan'])
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  function load() {
    Promise.all([api.get('/targets'), api.get('/jobs', { params: { limit: 15 } })])
      .then(([t, j]) => { setTargets(t.data); setJobs(j.data) })
      .catch((e) => setError(errorMessage(e)))
  }
  useEffect(load, [])

  async function addTarget(e) {
    e.preventDefault()
    setError(null)
    try {
      await api.post('/targets', { host: form.host.trim(), port: Number(form.port) || 443, server_name: form.server_name.trim() || null, environment: form.environment || null })
      setForm(EMPTY); load()
    } catch (err) { setError(errorMessage(err, 'Could not add the target')) }
  }

  async function removeTarget(id) {
    try { await api.delete(`/targets/${id}`); load() } catch (err) { setError(errorMessage(err)) }
  }

  async function discover() {
    setBusy(true); setResult(null); setError(null)
    try { setResult((await api.post('/cloud/discover', { providers: selected })).data); load() }
    catch (err) { setError(errorMessage(err, 'Discovery failed')) }
    finally { setBusy(false) }
  }

  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2"><Cloud size={20} className="text-accent" /> Sources</h1>
        <p className="text-muted text-sm mt-1">Where certificates come from. Providers that are not configured are skipped, not failed.</p>
      </div>
      {error && <p role="alert" className="text-danger text-sm">{error}</p>}

      <section className="card" aria-labelledby="disc-h">
        <h2 id="disc-h" className="text-sm font-medium mb-3">Run discovery</h2>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {PROVIDERS.map(([id, label]) => (
            <label key={id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} /> {label}</label>
          ))}
        </div>
        {isOperator
          ? <button onClick={discover} disabled={busy || !selected.length} className="mt-3 bg-accent text-bg font-medium rounded-lg px-4 py-2 text-sm disabled:opacity-40">{busy ? 'Discovering…' : 'Run discovery'}</button>
          : <p className="text-muted text-xs mt-3">Operators can run discovery.</p>}
        {result && (
          <ul className="mt-3 text-sm space-y-1" aria-label="Discovery result">
            {Object.entries(result).map(([p, r]) => (
              <li key={p}><span className="font-medium">{p}</span>: <span className={JOB_TONE[r.status]}>{r.status}</span>, {r.discovered ?? 0} found
                {r.errors?.length ? <span className="text-muted"> · {r.errors.slice(0, 2).join('; ')}</span> : null}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-labelledby="tgt-h">
        <h2 id="tgt-h" className="text-sm font-medium mb-3">Network scan targets</h2>
        {isOperator && (
          <form onSubmit={addTarget} className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-4 items-end">
            <input aria-label="Host" required placeholder="host or IP" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm" />
            <input aria-label="Port" type="number" min="1" max="65535" value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm" />
            <input aria-label="Server name" placeholder="SNI / hostname (optional)" value={form.server_name} onChange={(e) => setForm({ ...form, server_name: e.target.value })} className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm" />
            <select aria-label="Target environment" value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })} className="bg-panel2 border border-border rounded-lg px-3 py-2 text-sm">
              <option value="">env: none</option><option value="prod">prod</option><option value="staging">staging</option><option value="dev">dev</option>
            </select>
            <button className="bg-accent text-bg rounded-lg px-3 py-2 text-sm">Add target</button>
          </form>
        )}
        {targets.length === 0 ? <p className="text-muted text-sm">No targets yet.</p> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-muted border-b border-border"><th className="py-2">Target</th><th>Env</th><th>Last scanned</th><th>Last error</th><th /></tr></thead>
            <tbody>
              {targets.map((t) => (
                <tr key={t.id} className="border-b border-border/60 last:border-0">
                  <td className="py-2">{t.host}:{t.port}{t.server_name ? <span className="text-muted"> · {t.server_name}</span> : null}</td>
                  <td className="text-muted">{t.environment || '—'}</td>
                  <td className="text-muted">{t.last_scanned_at ? new Date(t.last_scanned_at).toLocaleString() : 'never'}</td>
                  <td className="text-danger max-w-xs truncate" title={t.last_error || ''}>{t.last_error || ''}</td>
                  <td className="text-right">{isOperator && <button aria-label={`Remove ${t.host}:${t.port}`} onClick={() => removeTarget(t.id)} className="text-muted hover:text-danger"><Trash2 size={15} /></button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card" aria-labelledby="jobs-h">
        <h2 id="jobs-h" className="text-sm font-medium mb-3">Recent runs</h2>
        {jobs.length === 0 ? <p className="text-muted text-sm">No runs yet.</p> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-muted border-b border-border"><th className="py-2">Started</th><th>Provider</th><th>Status</th><th>Attempts</th><th>Found</th><th>By</th></tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="border-b border-border/60 last:border-0" title={j.errors.join('\n')}>
                  <td className="py-2 text-muted whitespace-nowrap">{new Date(j.started_at).toLocaleString()}</td>
                  <td>{j.provider}</td><td className={JOB_TONE[j.status]}>{j.status}</td><td>{j.attempts}</td><td>{j.discovered}</td><td className="text-muted">{j.triggered_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
