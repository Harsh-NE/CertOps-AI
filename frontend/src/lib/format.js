// Pure helpers shared by pages and components (unit-tested).
export const MS_PER_DAY = 86_400_000

/** Whole days from `now` until `notAfter` (negative when past); null when there is no date. */
export function daysLeft(notAfter, now = new Date()) {
  if (!notAfter) return null
  return Math.floor((new Date(notAfter) - now) / MS_PER_DAY)
}

export function expiryLabel(days) {
  if (days === null || days === undefined) return 'No expiry data'
  if (days < 0) return `Expired ${Math.abs(days)}d ago`
  if (days === 0) return 'Expires today'
  return `Expires in ${days}d`
}

/** Colour class for the time left: red under a week, amber under a month. */
export function expiryTone(days) {
  if (days === null || days === undefined) return 'text-muted'
  if (days < 7) return 'text-danger'
  if (days < 30) return 'text-warn'
  return 'text-muted'
}

const STATUS_TONES = {
  valid: 'good',
  expiring_soon: 'warn',
  expired: 'danger',
  revoked: 'danger',
  invalid_chain: 'danger',
  hostname_mismatch: 'warn',
  weak_crypto: 'warn',
  unknown: 'neutral',
}
export function statusTone(status) {
  return STATUS_TONES[status] || 'neutral'
}
export function statusLabel(status) {
  return (status || 'unknown').replace(/_/g, ' ')
}

/** Query string from a filter object, skipping empty values. */
export function toQuery(params) {
  const p = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) p.append(k, v)
  })
  return p.toString()
}

export function shortSerial(s) {
  return s ? (s.length > 10 ? `${s.slice(0, 10)}…` : s) : '—'
}

/** Pull a readable message out of an axios error (FastAPI `detail` is a string, object or list). */
export function errorMessage(e, fallback = 'Something went wrong') {
  const d = e?.response?.data?.detail
  if (!d) return e?.message || fallback
  if (typeof d === 'string') return d
  if (d.message) return d.message
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join('; ')
  return JSON.stringify(d)
}
