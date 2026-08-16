import React, { useState, useRef, useEffect } from 'react'
import api from '../api.js'
import { Send, Bot, User } from 'lucide-react'

const SUGGESTIONS = [
  'Which certificates expire within 30 days?',
  'Why is app3.lab.local failing validation?',
  'Show certificates issued by CertPlatform Lab expiring this quarter.',
  'Recommend remediation for legacy.lab.local',
]

export default function AgentChat() {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: 'Ask me about certificate expiry, validation failures, or remediation steps. I use the platform\'s live data — nothing is guessed.' },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  async function send(text) {
    const question = (text ?? input).trim()
    if (!question) return
    setMessages((m) => [...m, { role: 'user', text: question }])
    setInput('')
    setBusy(true)
    try {
      const res = await api.post('/agent/query', { question })
      setMessages((m) => [...m, { role: 'assistant', text: res.data.answer, tools: res.data.used_tools }])
    } catch (e) {
      setMessages((m) => [...m, { role: 'assistant', text: 'Something went wrong reaching the agent.' }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto flex flex-col h-[calc(100vh-160px)]">
      <div className="mb-4">
        <h1 className="text-xl font-semibold flex items-center gap-2"><Bot size={20} className="text-accent" /> AI Agent</h1>
        <p className="text-muted text-sm mt-1">Root-cause analysis, remediation plans, and natural-language queries over your certificate fleet.</p>
      </div>

      <div className="flex-1 overflow-y-auto space-y-4 pr-1">
        {messages.map((m, i) => (
          <div key={i} className={`flex gap-2 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {m.role === 'assistant' && <Bot size={18} className="text-accent mt-1 shrink-0" />}
            <div className={`rounded-xl px-4 py-2.5 text-sm max-w-[80%] whitespace-pre-wrap ${
              m.role === 'user' ? 'bg-accent text-bg' : 'bg-panel border border-border'
            }`}>
              {m.text}
              {m.tools?.length > 0 && (
                <p className="text-xs mt-2 opacity-60">tools used: {m.tools.join(', ')}</p>
              )}
            </div>
            {m.role === 'user' && <User size={18} className="text-muted mt-1 shrink-0" />}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="flex flex-wrap gap-2 my-3">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => send(s)} className="text-xs bg-panel2 border border-border rounded-full px-3 py-1.5 hover:border-accent transition">
            {s}
          </button>
        ))}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send() }} className="flex items-center gap-2">
        <input
          className="flex-1 bg-panel2 border border-border rounded-lg px-3 py-2.5 text-sm outline-none focus:border-accent"
          placeholder="Ask about your certificates…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button disabled={busy} className="bg-accent text-bg rounded-lg px-4 py-2.5 hover:opacity-90 transition">
          <Send size={16} />
        </button>
      </form>
    </div>
  )
}
