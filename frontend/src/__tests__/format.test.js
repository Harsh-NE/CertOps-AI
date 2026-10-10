import { describe, expect, it } from 'vitest'
import { daysLeft, errorMessage, expiryLabel, expiryTone, shortSerial, statusLabel, statusTone, toQuery } from '../lib/format.js'

const NOW = new Date('2026-06-01T12:00:00Z')

describe('daysLeft', () => {
  it('counts whole days until expiry', () => {
    expect(daysLeft('2026-06-11T12:00:00Z', NOW)).toBe(10)
    expect(daysLeft('2026-06-11T11:00:00Z', NOW)).toBe(9)
  })
  it('is negative for past dates and null without a date', () => {
    expect(daysLeft('2026-05-30T12:00:00Z', NOW)).toBe(-2)
    expect(daysLeft(null, NOW)).toBeNull()
    expect(daysLeft(undefined, NOW)).toBeNull()
  })
})

describe('expiryLabel and expiryTone', () => {
  it.each([[null, 'No expiry data'], [-3, 'Expired 3d ago'], [0, 'Expires today'], [14, 'Expires in 14d']])('labels %s', (d, text) => {
    expect(expiryLabel(d)).toBe(text)
  })
  it.each([[3, 'text-danger'], [6, 'text-danger'], [7, 'text-warn'], [29, 'text-warn'], [30, 'text-muted'], [null, 'text-muted']])('colours %s days', (d, cls) => {
    expect(expiryTone(d)).toBe(cls)
  })
})

describe('status helpers', () => {
  it('maps statuses to tones, defaulting unknown values to neutral', () => {
    expect(statusTone('valid')).toBe('good')
    expect(statusTone('expired')).toBe('danger')
    expect(statusTone('hostname_mismatch')).toBe('warn')
    expect(statusTone('something_new')).toBe('neutral')
  })
  it('humanises status names', () => {
    expect(statusLabel('invalid_chain')).toBe('invalid chain')
    expect(statusLabel(undefined)).toBe('unknown')
  })
})

describe('toQuery', () => {
  it('skips empty, null and undefined values but keeps zero and false', () => {
    expect(toQuery({ a: 'x', b: '', c: null, d: undefined, e: 0, f: false })).toBe('a=x&e=0&f=false')
  })
  it('encodes values', () => {
    expect(toQuery({ q: "let's encrypt" })).toBe('q=let%27s+encrypt')
  })
})

describe('shortSerial', () => {
  it('truncates long serials and dashes missing ones', () => {
    expect(shortSerial('5de9eb2ece1234567')).toBe('5de9eb2ece…')
    expect(shortSerial('abc')).toBe('abc')
    expect(shortSerial(null)).toBe('—')
  })
})

describe('errorMessage', () => {
  const err = (detail) => ({ response: { data: { detail } } })
  it('handles string, object and list details', () => {
    expect(errorMessage(err('boom'))).toBe('boom')
    expect(errorMessage(err({ code: 'x', message: 'approval required' }))).toBe('approval required')
    expect(errorMessage(err([{ msg: 'field required' }, { msg: 'bad value' }]))).toBe('field required; bad value')
  })
  it('falls back to the error message, then the supplied default', () => {
    expect(errorMessage(new Error('network down'))).toBe('network down')
    expect(errorMessage({}, 'fallback')).toBe('fallback')
    expect(errorMessage(undefined)).toBe('Something went wrong')
  })
})
