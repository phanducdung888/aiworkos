import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, expect } from 'vitest'
import * as matchers from 'vitest-axe/matchers'

expect.extend(matchers)
afterEach(cleanup)

// jsdom has no crypto.randomUUID in every version; the idempotency key generator needs one.
if (!globalThis.crypto?.randomUUID) {
  Object.defineProperty(globalThis, 'crypto', {
    value: { ...globalThis.crypto, randomUUID: () => '00000000-0000-4000-8000-000000000000' },
  })
}
