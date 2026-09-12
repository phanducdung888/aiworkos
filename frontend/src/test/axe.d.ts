/**
 * `vitest-axe` ships its matchers but not their types for Vitest's `expect`.
 *
 * Declared here so `toHaveNoViolations` typechecks. Without it the accessibility assertions still
 * run and still fail the suite, but `npm run typecheck` complains — and a typecheck that has to be
 * ignored is one nobody reads.
 */
import 'vitest'
import type { AxeMatchers } from 'vitest-axe/matchers'

declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface Assertion extends AxeMatchers {}
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface AsymmetricMatchersContaining extends AxeMatchers {}
}
