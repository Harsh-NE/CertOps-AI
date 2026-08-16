import React from 'react'

export default function StatCard({ label, value, tone = 'default', icon: Icon }) {
  const toneClass = {
    default: 'text-slate-100',
    warn: 'text-warn',
    danger: 'text-danger',
    good: 'text-accent2',
  }[tone]

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-muted uppercase tracking-wide">{label}</span>
        {Icon && <Icon size={16} className="text-muted" />}
      </div>
      <div className={`text-3xl font-semibold ${toneClass}`}>{value}</div>
    </div>
  )
}
