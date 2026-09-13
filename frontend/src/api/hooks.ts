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
export type EventDetail = Schemas['EventDetail']
export type EventCapture = Schemas['EventCapture']
export type ParticipantInput = Schemas['ParticipantInputModel']
export type ParticipantRole = Schemas['ParticipantRole']
export type Analysis = Schemas['AnalysisResource']
export type Proposal = Schemas['ProposalResource']
export type ProposalDetail = Schemas['ProposalDetail']
export type ApprovalRecord = Schemas['ApprovalRecordResource']
export type Decision = Schemas['Decision']
export type ProposalStatus = Schemas['ProposalStatus']
export type Evidence = Schemas['EvidenceResource']
export type EventAttachment = Schemas['EventAttachmentResource']
export type AttachmentContent = Schemas['AttachmentContentResource']
export type Commitment = Schemas['CommitmentResource']
export type CommitmentStatus = Schemas['CommitmentStatus']
export type AIInteraction = Schemas['AIInteractionDetail']
export type CapabilityPolicy = Schemas['CapabilityPolicyResource']
export type PolicyCell = Schemas['PolicyCellResource']
export type AutonomyMode = Schemas['AutonomyMode']
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
  event: (id: string) => ['events', id] as const,
  proposals: (status: string) => ['proposals', status] as const,
  proposal: (id: string) => ['proposals', id] as const,
  evidence: (id: string) => ['evidence', id] as const,
  approval: (id: string) => ['approvals', id] as const,
  commitment: (id: string) => ['commitments', id] as const,
  commitments: (filters: CommitmentFilters) => ['commitments', 'list', filters] as const,
  overdue: ['commitments', 'overdue'] as const,
  producedBy: (entityId: string) => ['proposals', 'produced', entityId] as const,
  interaction: (id: string) => ['ai-interactions', id] as const,
  agentPolicy: ['agent-policy'] as const,
}

export interface WorkFilters {
  status?: WorkStatus
  partition?: 'project' | 'non_project'
  project_id?: string
  owner_person_id?: string
  /** `due_date < this`. The server compares dates; the caller decides what "soon" means. */
  due_before?: string
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
// --------------------------------------------------------------------------- capture and review
//
// The Level 1 loop: an Event is captured, an analysis proposes, and a person decides. Nothing here
// executes anything — an approved Proposal is queued and a worker runs it (ADR-0044, ADR-0048), so
// the screen polls the ApprovalRecord rather than waiting on a response that will never carry the
// result.

export function useEvent(id: string): UseQueryResult<EventDetail> {
  return useQuery({
    queryKey: keys.event(id),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/events/{event_id}', { params: { path: { event_id: id } } })),
    enabled: Boolean(id),
  })
}

/**
 * Ask for a short-lived URL to one attachment's bytes.
 *
 * A mutation rather than a query, because it is not idempotent in the sense that matters: each call
 * mints a fresh credential with its own expiry, and caching one would hand out a URL that has since
 * died. Nothing here is cached for the same reason.
 */
export function useAttachmentContent(
  eventId: string,
): UseMutationResult<AttachmentContent, Error, string> {
  return useMutation({
    mutationFn: async (attachmentId: string) =>
      unwrap(
        await api.GET('/api/v1/events/{event_id}/attachments/{attachment_id}/content', {
          params: { path: { event_id: eventId, attachment_id: attachmentId } },
        }),
      ),
  })
}

export function useProposals(status: ProposalStatus = 'pending'): UseQueryResult<Proposal[]> {
  return useQuery({
    queryKey: keys.proposals(status),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/proposals', { params: { query: { status, limit: 200 } } }),
      ).items,
  })
}

export function useProposal(id: string): UseQueryResult<ProposalDetail> {
  return useQuery({
    queryKey: keys.proposal(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/proposals/{proposal_id}', {
          params: { path: { proposal_id: id } },
        }),
      ),
    enabled: Boolean(id),
  })
}

export function useEvidence(id: string | undefined): UseQueryResult<Evidence> {
  return useQuery({
    queryKey: keys.evidence(id ?? ''),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/evidence/{evidence_id}', {
          params: { path: { evidence_id: id as string } },
        }),
      ),
    enabled: Boolean(id),
  })
}

//: A worker picks the job up in well under a second when one is running. Bounded because the
//: interesting failure is that none is: a screen that polled forever would look like a slow
//: success rather than like nothing listening, and the reader deserves the second reading.
const POLL_MS = 2000
const POLL_LIMIT = 30

/**
 * The approval for a proposal, polled while its execution is still outstanding.
 *
 * Polling stops the moment the record reaches a terminal execution status, and gives up after a
 * minute either way. A screen that kept asking would be asking about something that can no longer
 * change — or about a worker that is not there.
 */
export function useApprovalFor(
  proposalId: string,
  enabled: boolean,
  { poll = true }: { poll?: boolean } = {},
): UseQueryResult<ApprovalRecord> {
  return useQuery({
    queryKey: keys.approval(proposalId),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/proposals/{proposal_id}/approval', {
          params: { path: { proposal_id: proposalId } },
        }),
      ),
    enabled: enabled && Boolean(proposalId),
    // A 404 here is an answer, not a failure: a pending proposal has no approval yet.
    retry: false,
    refetchInterval: (query) => {
      if (!poll) return false
      const status = query.state.data?.execution_status
      const outstanding = status === undefined || status === 'pending' || status === 'running'
      return outstanding && query.state.dataUpdateCount < POLL_LIMIT ? POLL_MS : false
    },
  })
}

export function useCommitment(id: string | null | undefined): UseQueryResult<Commitment> {
  return useQuery({
    queryKey: keys.commitment(id ?? ''),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/commitments/{commitment_id}', {
          params: { path: { commitment_id: id as string } },
        }),
      ),
    enabled: Boolean(id),
  })
}

export function useCaptureEvent(): UseMutationResult<EventDetail, Error, EventCapture> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (body) =>
      unwrap(
        await api.POST('/api/v1/events', {
          body,
          headers: { 'Idempotency-Key': idempotencyKey() },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['events'] }),
  })
}

export function useAnalyzeEvent(): UseMutationResult<Analysis, Error, { eventId: string }> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ eventId }) =>
      unwrap(
        await api.POST('/api/v1/events/{event_id}/analyze', {
          params: { path: { event_id: eventId } },
          // One key per action: a retried analysis replays its first answer instead of raising a
          // second set of Proposals from the same message.
          headers: { 'Idempotency-Key': idempotencyKey() },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['proposals'] }),
  })
}

export function useDecideProposal(): UseMutationResult<
  ApprovalRecord,
  Error,
  { proposal: ProposalDetail; decision: Decision; rejection_reason?: string }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ proposal, decision, rejection_reason }) =>
      unwrap(
        await api.POST('/api/v1/proposals/{proposal_id}/decision', {
          params: { path: { proposal_id: proposal.id } },
          // The version the reviewer read. A decision made from a stale screen is refused rather
          // than applied to a proposal that has since been revised (BR-PR-06).
          headers: ifMatch(proposal.version),
          body: { decision, rejection_reason: rejection_reason ?? null },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['proposals'] }),
  })
}

/** Hand an approved action to the queue. The worker performs it; this returns before it runs. */
export function useQueueApproval(): UseMutationResult<unknown, Error, { approvalId: string }> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ approvalId }) =>
      api.POST('/api/v1/approvals/{approval_id}/queue', {
        params: { path: { approval_id: approvalId } },
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['approvals'] }),
  })
}
// --------------------------------------------------------------------------- commitments

export interface CommitmentFilters {
  status?: CommitmentStatus
  committed_by_person_id?: string
  committed_to_person_id?: string
  due_before?: string
}

export function useCommitments(filters: CommitmentFilters = {}): UseQueryResult<Commitment[]> {
  return useQuery({
    queryKey: keys.commitments(filters),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/commitments', { params: { query: { ...filters, limit: 200 } } }))
        .items,
  })
}

/**
 * BR-C-06 as a question. Nothing is written by asking it.
 *
 * Vague-precision promises are absent by rule — a deadline nobody set cannot be missed — which is
 * why reading a date out of the message at all is worth doing (ADR-0055).
 */
export function useOverdueCommitments(): UseQueryResult<Commitment[]> {
  return useQuery({
    queryKey: keys.overdue,
    queryFn: async () => unwrap(await api.GET('/api/v1/commitments/overdue/today', {})).items,
  })
}

export function useChangeCommitmentStatus(): UseMutationResult<
  Commitment,
  Error,
  { commitment: Commitment; target: CommitmentStatus; new_due_date?: string }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ commitment, target, new_due_date }) =>
      unwrap(
        await api.POST('/api/v1/commitments/{commitment_id}/status', {
          params: { path: { commitment_id: commitment.id } },
          // The version the reader saw. BR-C-04 is about a transition from a state, and a decision
          // made against a state that has since changed is about the wrong row.
          headers: ifMatch(commitment.version),
          body: { target, new_due_date: new_due_date ?? null },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['commitments'] }),
  })
}

// --------------------------------------------------------------------------- provenance
//
// ADR-0056: the chain is walked, never copied. Entity -> Proposal -> Approval -> Evidence -> Event,
// each hop an endpoint that already existed.

/** The Proposal whose approved execution produced this entity, if one did. */
export function useProducedBy(entityId: string): UseQueryResult<Proposal | null> {
  return useQuery({
    queryKey: keys.producedBy(entityId),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/proposals', {
          params: { query: { resulting_entity_id: entityId, limit: 1 } },
        }),
      ).items[0] ?? null,
    enabled: Boolean(entityId),
  })
}

export function useAiInteraction(id: string | null | undefined): UseQueryResult<AIInteraction> {
  return useQuery({
    queryKey: keys.interaction(id ?? ''),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/ai-interactions/{interaction_id}', {
          params: { path: { interaction_id: id as string } },
        }),
      ),
    enabled: Boolean(id),
  })
}

/**
 * What this organization has decided its agents may do (ADR-0047).
 *
 * Worth reading on the capture screen, because absence is denial: an organization that has decided
 * nothing denies everything, and an analysis that proposes nothing then looks like a model that
 * found nothing. Those are different facts and a person has to be able to tell them apart.
 */
export function useAgentPolicy(): UseQueryResult<CapabilityPolicy[]> {
  return useQuery({
    queryKey: keys.agentPolicy,
    queryFn: async () => unwrap(await api.GET('/api/v1/agent-policy', {})).items,
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * The whole decidable grid, each cell carrying its effective mode.
 *
 * The surface comes from the server, never from a list in this file. A client that hard-coded the
 * capabilities would offer switches a future build had removed, and would miss ones it had gained
 * — and the thing being edited is an authorization policy.
 */
export function useAgentPolicyGrid(): UseQueryResult<PolicyCell[]> {
  return useQuery({
    queryKey: [...keys.agentPolicy, 'grid'],
    queryFn: async () => unwrap(await api.GET('/api/v1/agent-policy', {})).available ?? [],
  })
}

export function useSetAgentPolicy(): UseMutationResult<
  CapabilityPolicy,
  Error,
  { cell: PolicyCell; mode: AutonomyMode; reason?: string }
> {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ cell, mode, reason }) =>
      unwrap(
        await api.PUT('/api/v1/agent-policy', {
          body: {
            capability: cell.capability as never,
            entity_type: cell.entity_type,
            action: cell.action as never,
            mode,
            reason: reason ?? null,
          },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.agentPolicy }),
  })
}
