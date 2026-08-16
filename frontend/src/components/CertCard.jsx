import React from 'react'
import { Link } from 'react-router-dom'
import RiskBadge from './RiskBadge.jsx'

function daysLeft(notAfter) {
  if (!notAfter) return null
  const d = Math.floor((new Date(notAfter) - new Date()) / (1000 * 60 * 60 * 24))
  return d
}

export default function CertCard({ cert }) {
  const d = daysLeft(cert.not_after)
  return (
    <Link to={`/certificates/${cert.id}`} className="card block hover:border-accent/50 transition">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="font-medium text-sm">{cert.common_name}</p>
          <p className="text-xs text-muted mt-0.5">{cert.source} · {cert.source_ref}</p>
        </div>
        <RiskBadge tier={cert.risk_tier} />
      </div>
      <div className="flex items-center justify-between mt-3 text-xs">
        <span className={d !== null && d < 7 ? 'text-danger' : d !== null && d < 30 ? 'text-warn' : 'text-muted'}>
          {d === null ? 'No expiry data' : d < 0 ? `Expired ${Math.abs(d)}d ago` : `Expires in ${d}d`}
        </span>
        <span className="text-muted">{cert.owner?.name || 'Unassigned'}</span>
      </div>
    </Link>
  )
}
