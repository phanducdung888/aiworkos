/**
 * Capture.
 *
 * A title, and nothing else required. Project and assignment are both optional here because they
 * are optional in the domain (ADR-0029, BR-W-07, BR-W-15) — a form that demanded either would be
 * the UI path those rules exist to forbid, and "someone should check the IOC API" would have
 * nowhere to land.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCreateWork, useProjects } from '@/api/hooks'
import { ErrorState } from '@/components/States'
import { Field } from '@/components/Field'

export function CreateWork() {
  const [title, setTitle] = useState('')
  const [projectId, setProjectId] = useState('')
  const projects = useProjects()
  const create = useCreateWork()
  const navigate = useNavigate()

  return (
    <section aria-labelledby="capture-heading">
      <h1 id="capture-heading">Capture work</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate(
            { title, project_id: projectId || null },
            { onSuccess: (work) => navigate(`/work/${work.id}`) },
          )
        }}
      >
        <Field label="Title">
          {(id) => (
            <input
              id={id}
              required
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Check the IOC API"
            />
          )}
        </Field>

        <Field label="Project" hint="Optional. Work with no project is a complete record.">
          {(id) => (
            <select id={id} value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">No project</option>
              {(projects.data ?? []).map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          )}
        </Field>

        <button type="submit" disabled={create.isPending || title.trim() === ''}>
          {create.isPending ? 'Capturing…' : 'Capture'}
        </button>
      </form>

      {create.isError ? <ErrorState error={create.error} /> : null}
    </section>
  )
}
