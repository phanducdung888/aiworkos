/**
 * Server state, and only server state.
 *
 * Every list and entity here is owned by the API. None of it is copied into component state,
 * because a second copy is a second truth and they diverge the moment one of them is stale
 * (contract §14, architecture §8). Mutations invalidate rather than patch, so what the screen shows
 * after a write is what the server actually stored — including the fields it decided for us, like
 * a Work item's visibility inherited from its Project (BR-W-19).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'
import { api, idempotencyKey, ifMatch, unwrap } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type Work = Schemas['WorkResource']
export type WorkList = Schemas['WorkList']
export type Project = Schemas['ProjectResource']
export type Milestone = Schemas['MilestoneResource']
export type Dependency = Schemas['DependencyResource']
export type Assignment = Schemas['AssignmentResource']
export type Person = Schemas['PersonResource']
export type Team = Schemas['TeamResource']
export type Me = Schemas['CurrentPrincipal']
/**
 * The enums, taken from the request schemas rather than the response ones.
 *
 * A response serialises `status` as a plain string — the column is text with a CHECK constraint —
 * while the request side is a closed enum. Using the request type everywhere means a typo in a
 * filter or a transition target is caught by `tsc` instead of by a 422 at runtime.
 */
export type WorkStatus = Schemas['WorkStatus']
export type AssignmentRole = Schemas['AssignmentRole']

export const keys = {
  me: ['me'] as const,
  people: (q?: string) => ['people', q ?? ''] as const,
  teams: ['teams'] as const,
  work: (filters: WorkFilters) => ['work', filters] as const,
  workItem: (id: string) => ['work', 'item', id] as const,
  assignments: (id: string) => ['work', 'item', id, 'assignments'] as const,
  dependencies: (id: string) => ['work', 'item', id, 'dependencies'] as const,
  projects: ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  milestones: (projectId: string) => ['projects', projectId, 'milestones'] as const,
}

export interface WorkFilters {
  status?: WorkStatus
  partition?: 'project' | 'non_project'
  project_id?: string
  owner_person_id?: string
}

export function useMe(enabled = true): UseQueryResult<Me> {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => unwrap(await api.GET('/api/v1/me', {})),
    // Asking who you are before you are signed in is a request that can only be refused.
    enabled,
    // Identity changes far less often than work does, and every surface asks for it.
    staleTime: 5 * 60 * 1000,
  })
}

export function usePeople(query?: string): UseQueryResult<Person[]> {
  return useQuery({
    queryKey: keys.people(query),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/people', { params: { query: { q: query, limit: 200 } } }))
        .items,
    staleTime: 60 * 1000,
  })
}

export function useTeams(): UseQueryResult<Team[]> {
  return useQuery({
    queryKey: keys.teams,
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/teams', { params: { query: { limit: 200 } } })).items,
    staleTime: 60 * 1000,
  })
}

export function useWork(filters: WorkFilters = {}): UseQueryResult<Work[]> {
  return useQuery({
    queryKey: keys.work(filters),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/work', { params: { query: { ...filters, limit: 200 } } }))
        .items,
  })
}

export function useWorkItem(id: string): UseQueryResult<Work> {
  return useQuery({
    queryKey: keys.workItem(id),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/work/{work_id}', { params: { path: { work_id: id } } })),
  })
}

export function useAssignments(workId: string): UseQueryResult<Assignment[]> {
  return useQuery({
    queryKey: keys.assignments(workId),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/work/{work_id}/assignments', {
          params: { path: { work_id: workId } },
        }),
      ).items,
  })
}

export function useDependencies(workId: string): UseQueryResult<Dependency[]> {
  return useQuery({
    queryKey: keys.dependencies(workId),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/work/{work_id}/dependencies', {
          params: { path: { work_id: workId } },
        }),
      ).items,
  })
}

export function useProjects(): UseQueryResult<Project[]> {
  return useQuery({
    queryKey: keys.projects,
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/projects', { params: { query: { limit: 200 } } })).items,
  })
}

export function useMilestones(projectId: string): UseQueryResult<Milestone[]> {
  return useQuery({
    queryKey: keys.milestones(projectId),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/projects/{project_id}/milestones', {
          params: { path: { project_id: projectId } },
        }),
      ).items,
    enabled: Boolean(projectId),
  })
}

// --------------------------------------------------------------------------- mutations

type CreateWorkBody = Schemas['WorkCreate']

export function useCreateWork(): UseMutationResult<Work, Error, CreateWorkBody> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (body: CreateWorkBody) =>
      unwrap(
        await api.POST('/api/v1/work', {
          body,
          // One key per action, so a retry after a timeout replays the first answer instead of
          // creating a second Work item (W-6).
          headers: { 'Idempotency-Key': idempotencyKey() },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['work'] }),
  })
}

export function useChangeWorkStatus(): UseMutationResult<
  Work,
  Error,
  { work: Work; target: WorkStatus; blocked_reason?: string }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ work, target, blocked_reason }) =>
      unwrap(
        await api.POST('/api/v1/work/{work_id}/status', {
          params: { path: { work_id: work.id } },
          // The version we read, so a status change made from a stale screen is refused rather than
          // silently overwriting somebody else's (BR-G-06).
          headers: ifMatch(work.version),
          body: { target, blocked_reason: blocked_reason ?? null },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['work'] }),
  })
}

export function useUpdateWork(): UseMutationResult<
  Work,
  Error,
  { work: Work; changes: Schemas['WorkUpdate'] }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ work, changes }) =>
      unwrap(
        await api.PATCH('/api/v1/work/{work_id}', {
          params: { path: { work_id: work.id } },
          headers: ifMatch(work.version),
          body: changes,
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['work'] }),
  })
}

export function useAssign(): UseMutationResult<
  Assignment,
  Error,
  { workId: string; person_id: string; role: AssignmentRole }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ workId, person_id, role }) =>
      unwrap(
        await api.POST('/api/v1/work/{work_id}/assignments', {
          params: { path: { work_id: workId } },
          headers: { 'Idempotency-Key': idempotencyKey() },
          body: { person_id, role, is_primary: false },
        }),
      ),
    onSuccess: (_data, variables) =>
      void client.invalidateQueries({ queryKey: keys.assignments(variables.workId) }),
  })
}

export function useSetOwner(): UseMutationResult<
  Assignment,
  Error,
  { workId: string; person_id: string }
> {
  const client = useQueryClient()
  return useMutation({
    // One call, not end-then-assign: the two-step route around REASSIGN is exactly what the
    // authorization model refuses (ADR-0032).
    mutationFn: async ({ workId, person_id }) =>
      unwrap(
        await api.PUT('/api/v1/work/{work_id}/owner', {
          params: { path: { work_id: workId } },
          body: { person_id, is_primary: false },
        }),
      ),
    onSuccess: (_data, variables) =>
      void client.invalidateQueries({ queryKey: keys.assignments(variables.workId) }),
  })
}

export function useEndAssignment(): UseMutationResult<
  Assignment,
  Error,
  { workId: string; assignment: Assignment }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ workId, assignment }) =>
      unwrap(
        await api.DELETE('/api/v1/work/{work_id}/assignments/{assignment_id}', {
          params: { path: { work_id: workId, assignment_id: assignment.id } },
          headers: ifMatch(assignment.version),
        }),
      ),
    onSuccess: (_data, variables) =>
      void client.invalidateQueries({ queryKey: keys.assignments(variables.workId) }),
  })
}

export function useCreateProject(): UseMutationResult<Project, Error, Schemas['ProjectCreate']> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (body) =>
      unwrap(
        await api.POST('/api/v1/projects', {
          body,
          headers: { 'Idempotency-Key': idempotencyKey() },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.projects }),
  })
}

export function useCreateDependency(): UseMutationResult<
  Dependency,
  Error,
  Schemas['DependencyCreateRequest'] & { blockedWorkId: string }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ blockedWorkId: _ignored, ...body }) =>
      unwrap(
        await api.POST('/api/v1/dependencies', {
          body,
          headers: { 'Idempotency-Key': idempotencyKey() },
        }),
      ),
    onSuccess: (_data, variables) =>
      void client.invalidateQueries({ queryKey: keys.dependencies(variables.blockedWorkId) }),
  })
}
