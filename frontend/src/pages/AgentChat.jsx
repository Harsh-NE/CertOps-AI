import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bot, Send, Trash2, User } from 'lucide-react'
import api from '../api.js'
import { errorMessage } from '../lib/format.js'

const SUGGESTIONS = [
  'Which prod certificates expire in 14 days?',
  'Show me all expired certificates',
  'Why is selfsigned.lab.test failing validation?',
  'Create a renewal plan for healthy.lab.test',
]
const STORAGE_KEY = 'certops.chat.v1'
const WELCOME = { role: 'assistant', text: "Ask about expiry, validation failures, risk or renewals. I only answer from the platform's data, and I can't approve renewals: a human does that on the Renewals page." }

function load() {
  try { const v = JSON.parse(localStorage.getItem(STORAGE_KEY)); return Array.isArray(v) && v.length ? v : [WELCOME] } catch { return [WELCOME] }
}

export default function AgentChat() {
  const [messages, setMessages] = useState(load)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const endRef = useRef(null)

  useEffect(() => { api.get('/health').then((r) => setEnabled(!!r.data.ai_agent_enabled)).catch(() => {}) }, [])
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth' })
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-40))) } catch { /* storage may be unavailable */ }
  }, [messages])

  async function send(text) {
    const question = (text ?? input).trim()
    if (!question || busy) return
    setMessages((m) => [...m, { role: 'user', text: question }])
    setInput(''); setBusy(true)
    try {
      const res = await api.post('/agent/query', { question })
      setMessages((m) => [...m, { role: 'assistant', text: res.data.answer, calls: res.data.tool_calls || [], pending: res.data.pending_plans || 0, error: res.data.error }])
    } catch (e) {
      setMessages((m) => [...m, { role: 'assistant', text: errorMessage(e, 'The agent could not be reached.'), failed: true }])
    } finally { setBusy(false) }
  }

  return (
    <div className="max-w-3xl mx-auto flex flex-col h-[calc(100vh-160px)]">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2"><Bot size={20} className="text-accent" /> AI Agent</h1>
          <p className="text-muted text-sm mt-1">Natural-language queries, root-cause analysis and renewal planning over live platform data.</p>
        </div>
        <button aria-label="Clear conversation" onClick={() => setMessages([WELCOME])} className="text-muted hover:text-danger p-2"><Trash2 size={16} /></button>
      </div>
      {!enabled && (
        <p role="status" className="card text-sm text-warn mb-3">The agent is not configured: set GEMINI_API_KEY in .env and restart. The rest of the platform works without it.</p>
      )}

      <div className="flex-1 overflow-y-auto space-y-4 pr-1" aria-live="polite">
        {messages.map((m, i) => (
          <div key={i} className={`flex gap-2 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {m.role === 'assistant' && <Bot size={18} className="text-accent mt-1 shrink-0" />}
            <div className={`rounded-xl px-4 py-2.5 text-sm max-w-[80%] whitespace-pre-wrap ${m.role === 'user' ? 'bg-accent text-bg' : m.failed ? 'bg-danger/10 border border-danger/40' : 'bg-panel border border-border'}`}>
              {m.text}
              {m.calls?.length > 0 && (
                <p className="text-xs mt-2 opacity-80 flex flex-wrap gap-1" aria-label="Tools used">
                  {m.calls.map((c, j) => (
                    <span key={j} className={`px-1.5 py-0.5 rounded border ${c.status === 'ok' ? 'border-border' : 'border-danger text-danger'}`}>{c.tool}: {c.status}</span>
                  ))}
                </p>
              )}
              {m.pending > 0 && <p className="text-xs mt-1 text-warn">A renewal plan awaits human approval on the <Link className="underline" to="/renewals">Renewals page</Link>.</p>}
            </div>
            {m.role === 'user' && <User size={18} className="text-muted mt-1 shrink-0" />}
          </div>
        ))}
        {busy && <p className="text-muted text-sm flex items-center gap-2"><Bot size={16} className="text-accent" /> Thinking…</p>}
        <div ref={endRef} />
      </div>

      <div className="flex flex-wrap gap-2 my-3">
        {SUGGESTIONS.map((s) => (
          <button key={s} disabled={busy} onClick={() => send(s)} className="text-xs bg-panel2 border border-border rounded-full px-3 py-1.5 hover:border-accent transition disabled:opacity-40">{s}</button>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send() }} className="flex items-center gap-2">
        <input aria-label="Message" className="flex-1 bg-panel2 border border-border rounded-lg px-3 py-2.5 text-sm outline-none focus:border-accent" placeholder="Ask about your certificates…" value={input} onChange={(e) => setInput(e.target.value)} />
        <button aria-label="Send" disabled={busy || !input.trim()} className="bg-accent text-bg rounded-lg px-4 py-2.5 hover:opacity-90 transition disabled:opacity-40"><Send size={16} /></button>
      </form>
    </div>
  )
}
