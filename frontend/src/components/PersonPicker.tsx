/**
 * Choosing a person.
 *
 * Deliberately a plain `select` over the directory rather than a free-text id box. The API refuses
 * an unknown person, so a box that accepts one only moves the discovery of the mistake to after the
 * submit — and `/api/v1/people` already excludes departed people, who cannot take new work anyway
 * (BR-I-05).
 */
import { Field } from './Field'
import { Loading } from './States'
import { usePeople } from '@/api/hooks'

export function PersonPicker({
  label,
  value,
  onChange,
  exclude = [],
}: {
  label: string
  value: string
  onChange: (personId: string) => void
  exclude?: string[]
}) {
  const people = usePeople()
  if (people.isPending) return <Loading label="people" />
  const options = (people.data ?? []).filter((person) => !exclude.includes(person.id))

  return (
    <Field label={label}>
      {(id) => (
        <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Nobody</option>
          {options.map((person) => (
            <option key={person.id} value={person.id}>
              {person.display_name}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}
