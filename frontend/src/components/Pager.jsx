import React from 'react'

export default function Pager({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const from = total === 0 ? 0 : page * pageSize + 1
  const to = Math.min(total, (page + 1) * pageSize)
  return (
    <div className="flex items-center justify-between text-sm text-muted mt-3">
      <span>{total === 0 ? 'No results' : `${from}–${to} of ${total}`}</span>
      <div className="flex items-center gap-2">
        <button disabled={page === 0} onClick={() => onChange(page - 1)} className="px-3 py-1 rounded-lg border border-border disabled:opacity-40">Previous</button>
        <span>Page {page + 1} / {pages}</span>
        <button disabled={page + 1 >= pages} onClick={() => onChange(page + 1)} className="px-3 py-1 rounded-lg border border-border disabled:opacity-40">Next</button>
      </div>
    </div>
  )
}
