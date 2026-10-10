import React from 'react'
import { statusLabel, statusTone } from '../lib/format.js'

const TONE = {
  good: 'bg-accent2/10 text-accent2 border border-accent2/30',
  warn: 'bg-warn/10 text-warn border border-warn/30',
  danger: 'bg-danger/10 text-danger border border-danger/30',
  neutral: 'bg-muted/10 text-muted border border-muted/30',
}

export default function StatusBadge({ status }) {
  return <span className={`badge ${TONE[statusTone(status)]}`}>{statusLabel(status)}</span>
}
