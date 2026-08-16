import React, { useState } from 'react'
import api from '../api.js'
import { Cloud, CheckCircle2, XCircle } from 'lucide-react'

const PROVIDERS = [
  { id: 'aws_acm', label: 'AWS Certificate Manager' },
  { id: 'azure_key_vault', label: 'Azure Key Vault' },
  { id: 'gcp_cert_manager', label: 'Google Certificate Manager' },
  { id: 'kubernetes_secret', label: 'Kubernetes Secrets' },
]

export default function Settings() {
  const [selected, setSelected] = useState([])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  function toggle(id) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  }

  async function runDiscovery() {
    if (selected.length === 0) return
    setBusy(true)
    setResult(null)
    try {
      const res = await api.post('/cloud/discover', { providers: selected })
      setResult(res.data)
    } catch (e) {
      setResult({ error: e.response?.data?.detail || 'Discovery failed' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2"><Cloud size={20} className="text-accent" /> Multi-Cloud Discovery</h1>
      </div>

      <div className="card space-y-3">
        {PROVIDERS.map((p) => (
          <label key={p.id} className="flex items-center gap-3 text-sm cursor-pointer">
            <input type="checkbox" checked={selected.includes(p.id)} onChange={() => toggle(p.id)} className="accent-accent" />
            {p.label}
          </label>
        ))}
        <button
          onClick={runDiscovery}
          disabled={busy || selected.length === 0}
          className="mt-2 bg-accent text-bg font-medium rounded-lg px-4 py-2 text-sm hover:opacity-90 transition disabled:opacity-40"
        >
          {busy ? 'Discovering…' : 'Run discovery'}
        </button>
      </div>

      {result && (
        <div className="card space-y-2">
          <h2 className="text-sm font-medium mb-2">Result</h2>
          {result.error ? (
            <p className="text-danger text-sm">{result.error}</p>
          ) : (
            Object.entries(result).map(([provider, r]) => (
              <div key={provider} className="flex items-start gap-2 text-sm">
                {r.discovered > 0 ? <CheckCircle2 size={16} className="text-accent2 mt-0.5" /> : <XCircle size={16} className="text-muted mt-0.5" />}
                <div>
                  <p className="font-medium">{provider}</p>
                  <p className="text-muted text-xs">discovered: {r.discovered ?? 0}{r.errors?.length ? ` · errors: ${r.errors.join('; ')}` : ''}</p>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
