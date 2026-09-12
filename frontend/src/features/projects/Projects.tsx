/**
 * Projects and their milestones.
 *
 * The create form asks for an owning team because BR-P-01 requires one — and because the API will
 * refuse without it, a form that omitted the field would produce a rule violation the person could
 * not act on. The team list comes from the directory, so the choice is always a team that exists.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useCreateProject, useMilestones, useProjects, useTeams } from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Field } from '@/components/Field'

export function ProjectList() {
  const projects = useProjects()
  const teams = useTeams()
  const create = useCreateProject()
  const [name, setName] = useState('')
  const [teamId, setTeamId] = useState('')

  if (projects.isPending) return <Loading label="projects" />
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />

  const items = projects.data ?? []

  return (
    <section aria-labelledby="projects-heading">
      <h1 id="projects-heading">Projects</h1>

      {items.length === 0 ? (
        <Empty>No projects yet. Work does not need one.</Empty>
      ) : (
        <ul>
          {items.map((project) => (
            <li key={project.id}>
              <Link to={`/projects/${project.id}`}>{project.name}</Link> · {project.status} ·{' '}
              {project.visibility}
            </li>
          ))}
        </ul>
      )}

      <h2>New project</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate(
            { name, owning_team_id: teamId },
            { onSuccess: () => setName('') },
          )
        }}
      >
        <Field label="Name">
          {(id) => (
            <input id={id} required value={name} onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        <Field label="Owning team" hint="A project belongs to a team or a department (BR-P-01).">
          {(id) => (
            <select id={id} required value={teamId} onChange={(e) => setTeamId(e.target.value)}>
              <option value="">Choose a team</option>
              {(teams.data ?? []).map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        <button type="submit" disabled={create.isPending || !name.trim() || !teamId}>
          Create project
        </button>
      </form>
      {create.isError ? <ErrorState error={create.error} /> : null}
    </section>
  )
}

export function ProjectDetail() {
  const { projectId = '' } = useParams()
  const projects = useProjects()
  const milestones = useMilestones(projectId)
  const project = (projects.data ?? []).find((candidate) => candidate.id === projectId)

  if (projects.isPending) return <Loading label="the project" />
  if (projects.isError) return <ErrorState error={projects.error} />
  if (!project) return <Empty>That project is no longer available.</Empty>

  return (
    <article aria-labelledby="project-title">
      <h1 id="project-title">{project.name}</h1>
      <dl>
        <dt>Status</dt>
        <dd>{project.status}</dd>
        <dt>Visibility</dt>
        <dd data-testid="project-visibility">{project.visibility}</dd>
      </dl>

      <h2>Milestones</h2>
      {milestones.isPending ? <Loading label="milestones" /> : null}
      {milestones.isError ? <ErrorState error={milestones.error} /> : null}
      {milestones.data && milestones.data.length === 0 ? (
        <Empty>No milestones yet.</Empty>
      ) : (
        <ul>
          {(milestones.data ?? []).map((milestone) => (
            <li key={milestone.id}>
              {milestone.name} · {milestone.status} · {milestone.target_date ?? 'no target'}
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}
