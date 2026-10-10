import React from 'react'

export default function RiskBadge({ tier, score }) {
  const cls = tier ? `badge badge-${tier}` : 'badge badge-neutral'
  return (
    <span className={cls} title={score !== null && score !== undefined ? `risk score ${score}` : undefined}>
      {tier ? tier.toUpperCase() : 'UNSCORED'}
    </span>
  )
}
