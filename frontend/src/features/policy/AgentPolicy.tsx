/**
 * What this organization has decided its agents may do (ADR-0047, BR-AI-30).
 *
 * The screen is a grid because the policy is one: a row per `(capability, entity type, action)`
 * that the system has a tool for, and three modes it can be in. Nothing here is free text and
 * nothing here is invented by the client — the grid comes from the server, so a build that gains
 * or loses a capability changes this screen without anybody editing it.
 *
 * Two things it is careful to say out loud.
 *
 * **Absence is denial.** A cell nobody has decided is `off`, and it says *Not decided* rather than
 * showing a confident "off" — "somebody reviewed this and declined" and "nobody has looked" are
 * different facts about an organization, and only one of them means the safety review happened.
 *
 * **This is a ceiling, not a switch that makes things happen.** Turning a cell on lets an agent
 * *propose*; level 2 lets an approved proposal execute. Neither lets anything write without a
 * person, which is the part an administrator most needs to not have to guess about.
 */
import { useState } from 'react'
import type { AutonomyMode, PolicyCell } from '@/api/hooks'
import { useAgentPolicyGrid, usePeople, useSetAgentPolicy } from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Field } from '@/components/Field'
import { nameOf } from '@/components/Provenance'

/** The three modes, in the order they widen. Level 3 is not representable (BR-AI-31). */
export const MODES: { mode: AutonomyMode; label: string; meaning: string }[] = [
  { mode: 'off', label: 'Off', meaning: 'The agent may not do this at all.' },
  {
    mode: 'level_1_propose',
    label: 'Propose',
    meaning: 'The agent may raise a Proposal. Nothing is created until a person approves it.',
  },
  {
    mode: 'level_2_approved_execution',
    label: 'Execute once approved',
    meaning: 'An approved Proposal may be executed by the system. A person still approves first.',
  },
]

const formatted = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString() : '—'

export function AgentPolicy() {
  const grid = useAgentPolicyGrid()
  const people = usePeople()

  if (grid.isPending) return <Loading label="the agent policy" />
  if (grid.isError) return <ErrorState error={grid.error} retry={() => void grid.refetch()} />

  return (
    <section aria-labelledby="policy-heading">
      <h1 id="policy-heading">Agent policy</h1>
      <p>
        What agents in this organization may do. Everything not decided here is off — a new
        organization grants nothing, and that is the intended starting state.
      </p>
      <p className="field__hint">
        No setting on this page lets an agent write anything on its own. Level 1 lets it propose;
        level 2 lets a proposal a person has already approved be carried out.
      </p>

      {grid.data.length === 0 ? (
        <Empty>This build has no agent capability with a tool behind it.</Empty>
      ) : (
        <ul>
          {grid.data.map((cell) => (
            <Cell
              key={`${cell.capability}:${cell.entity_type}:${cell.action}`}
              cell={cell}
              decidedBy={nameOf(people.data, cell.decided_by_person_id ?? null)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function Cell({ cell, decidedBy }: { cell: PolicyCell; decidedBy: string }) {
  const [reason, setReason] = useState('')
  const set = useSetAgentPolicy()
  const testId = `${cell.entity_type}-${cell.action}`

  return (
    <li data-testid={`cell-${testId}`}>
      <h2>
        {cell.action} {cell.entity_type.replace('_', ' ')}
      </h2>
      <p className="field__hint">
        Capability {cell.capability} · turns on {cell.tools?.join(', ') || 'nothing'}
      </p>
      <p data-testid={`state-${testId}`}>
        {cell.decided ? (
          <>
            {MODES.find((candidate) => candidate.mode === cell.mode)?.label ?? cell.mode} · decided
            by {decidedBy} on {formatted(cell.updated_at)}
            {cell.reason ? ` · ${cell.reason}` : ''}
          </>
        ) : (
          // ADR-0047. Not the same statement as a decided `off`.
          <>Not decided, so off.</>
        )}
      </p>

      <Field label={`Reason for ${cell.action} ${cell.entity_type}`} hint="Optional, and the thing a reviewer wants six months later.">
        {(id) => (
          <input id={id} value={reason} onChange={(event) => setReason(event.target.value)} />
        )}
      </Field>
      {MODES.map((option) => (
        <button
          key={option.mode}
          type="button"
          aria-pressed={cell.decided && cell.mode === option.mode}
          disabled={set.isPending}
          title={option.meaning}
          onClick={() => set.mutate({ cell, mode: option.mode, reason: reason || undefined })}
        >
          {option.label}
        </button>
      ))}
      {set.isError ? <ErrorState error={set.error} /> : null}
    </li>
  )
}
