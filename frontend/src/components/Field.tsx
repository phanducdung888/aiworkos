/** A labelled control. Separate component so every field is labelled the same way, and is. */
import type { ReactNode } from 'react'
import { useId } from 'react'

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()
  const hintId = `${id}-hint`
  return (
    <p className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint ? (
        <span id={hintId} className="field__hint">
          {hint}
        </span>
      ) : null}
    </p>
  )
}
