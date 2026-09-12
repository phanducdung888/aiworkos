/**
 * The shell.
 *
 * Navigation is rendered from `/api/v1/me`, so a person sees the surfaces their roles reach. That
 * is guidance and nothing else: the backend re-decides every request, and a client that showed
 * every link would be refused rather than obeyed (contract §14).
 */
import { NavLink, Route, Routes } from 'react-router-dom'
import { useMe } from '@/api/hooks'
import { useAuth } from '@/auth/AuthProvider'
import { ErrorState, Loading } from '@/components/States'
import { Capture } from '@/features/capture/Capture'
import { CommitmentDetail, CommitmentList } from '@/features/commitments/Commitments'
import { ProposalDetail, ProposalList } from '@/features/proposals/Proposals'
import { CreateWork } from '@/features/work/CreateWork'
import { WorkDetail } from '@/features/work/WorkDetail'
import { WorkList } from '@/features/work/WorkList'
import { ProjectDetail, ProjectList } from '@/features/projects/Projects'

export function App() {
  const auth = useAuth()
  const me = useMe(auth.status === 'signed-in' && auth.organizationId !== null)

  if (auth.status === 'loading') return <Loading label="your session" />
  if (auth.status === 'error') {
    return <ErrorState error={new Error(auth.error ?? 'Sign-in failed.')} />
  }
  if (auth.status === 'signed-out') {
    return (
      <main>
        <h1>AI WorkOS</h1>
        <button type="button" onClick={auth.signIn}>
          Sign in
        </button>
      </main>
    )
  }
  if (!auth.organizationId) {
    return (
      <main>
        <h1>AI WorkOS</h1>
        {/* Never inferred, even from a single membership (security-model §2). */}
        <p>Choose the organization you are working in.</p>
      </main>
    )
  }
  if (me.isPending || me.isLoading) return <Loading label="your profile" />
  if (me.isError) return <ErrorState error={me.error} retry={() => void me.refetch()} />

  const canManageProjects = me.data.roles.some((role) =>
    ['org_admin', 'department_lead', 'team_lead'].includes(role),
  )

  return (
    <>
      <header>
        <nav aria-label="Main">
          <NavLink to="/work">Work</NavLink>
          <NavLink to="/work/new">New work</NavLink>
          <NavLink to="/capture">Capture message</NavLink>
          <NavLink to="/commitments">Commitments</NavLink>
          <NavLink to="/proposals">Proposals</NavLink>
          {canManageProjects ? <NavLink to="/projects">Projects</NavLink> : null}
        </nav>
        <p>
          <span data-testid="me-name">{me.data.display_name}</span>{' '}
          <button type="button" onClick={auth.signOut}>
            Sign out
          </button>
        </p>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<WorkList />} />
          <Route path="/work" element={<WorkList />} />
          <Route path="/work/new" element={<CreateWork />} />
          <Route path="/work/:workId" element={<WorkDetail />} />
          <Route path="/capture" element={<Capture />} />
          <Route path="/proposals" element={<ProposalList />} />
          <Route path="/proposals/:proposalId" element={<ProposalDetail />} />
          <Route path="/commitments" element={<CommitmentList />} />
          <Route path="/commitments/:commitmentId" element={<CommitmentDetail />} />
          <Route path="/projects" element={<ProjectList />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </main>
    </>
  )
}
