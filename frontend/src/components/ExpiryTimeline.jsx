import React from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export const TIER_COLORS = { low: '#7cf5c0', medium: '#ffb454', high: '#ff5c7a', critical: '#c23b57', unscored: '#7c8ba1' }
const TIERS = ['critical', 'high', 'medium', 'low', 'unscored']

/** Certificates per time-to-expiry bucket, stacked by risk tier. `buckets` comes from GET /risk/expiry-timeline. */
export default function ExpiryTimeline({ buckets }) {
  const total = (buckets || []).reduce((n, b) => n + b.total, 0)
  if (!total) return <p className="text-muted text-sm">No certificates with an expiry date yet.</p>
  return (
    <div role="img" aria-label="Certificates by time to expiry, stacked by risk tier">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={buckets} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid stroke="#243040" vertical={false} />
          <XAxis dataKey="label" stroke="#7c8ba1" fontSize={12} />
          <YAxis allowDecimals={false} stroke="#7c8ba1" fontSize={12} />
          <Tooltip contentStyle={{ background: '#121821', border: '1px solid #243040' }} cursor={{ fill: '#1a2230' }} />
          <Legend />
          {TIERS.map((t) => <Bar key={t} dataKey={t} stackId="a" fill={TIER_COLORS[t]} name={t} />)}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
