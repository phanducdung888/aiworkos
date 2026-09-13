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
      <h1 id="capture-heading">Tạo công việc</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate(
            { title, project_id: projectId || null },
            { onSuccess: (work) => navigate(`/work/${work.id}`) },
          )
        }}
      >
        <Field label="Tiêu đề">
          {(id) => (
            <input
              id={id}
              required
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Kiểm tra API của IOC"
            />
          )}
        </Field>

        <Field label="Dự án" hint="Không bắt buộc. Công việc không thuộc dự án nào vẫn là bản ghi đầy đủ.">
          {(id) => (
            <select id={id} value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">Không thuộc dự án nào</option>
              {(projects.data ?? []).map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          )}
        </Field>

        <button type="submit" disabled={create.isPending || title.trim() === ''}>
          {create.isPending ? 'Đang tạo…' : 'Tạo công việc'}
        </button>
      </form>

      {create.isError ? <ErrorState error={create.error} /> : null}
    </section>
  )
}
