import React from 'react'
import { Link } from 'react-router-dom'
import { daysLeft, expiryLabel, expiryTone } from '../lib/format.js'
import RiskBadge from './RiskBadge.jsx'

export default function CertCard({ cert }) {
  const d = daysLeft(cert.not_after)
  return (
    <Link to={`/certificates/${cert.id}`} className="card block hover:border-accent/50 transition">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="font-medium text-sm">{cert.common_name}</p>
          <p className="text-xs text-muted mt-0.5">{cert.environment || 'no env'} · {cert.source}</p>
        </div>
        <RiskBadge tier={cert.risk_tier} score={cert.risk_score} />
      </div>
      <div className="flex items-center justify-between mt-3 text-xs">
        <span className={expiryTone(d)}>{expiryLabel(d)}</span>
        <span className="text-muted">{cert.owner?.name || 'Unassigned'}</span>
      </div>
    </Link>
  )
}
