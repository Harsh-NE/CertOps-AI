import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Recharts' ResponsiveContainer needs ResizeObserver, which jsdom does not provide.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

configure({ asyncUtilTimeout: 5000 })

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.restoreAllMocks()
})
