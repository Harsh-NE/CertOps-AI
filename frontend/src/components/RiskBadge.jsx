import React from 'react'

export default function RiskBadge({ tier }) {
  const cls = tier ? `badge badge-${tier}` : 'badge badge-neutral'
  return <span className={cls}>{tier ? tier.toUpperCase() : 'UNSCORED'}</span>
}
