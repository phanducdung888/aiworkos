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
import { ChooseOrganization } from '@/auth/ChooseOrganization'
import { ErrorState, Loading } from '@/components/States'
import { AgentPolicy } from '@/features/policy/AgentPolicy'
import { Attention } from '@/features/attention/Attention'
import { Capture } from '@/features/capture/Capture'
import { MessageDetail, MessageList } from '@/features/messages/Messages'
import { People } from '@/features/people/People'
import { CommitmentDetail, CommitmentList } from '@/features/commitments/Commitments'
import { ProposalDetail, ProposalList } from '@/features/proposals/Proposals'
import { CreateWork } from '@/features/work/CreateWork'
import { WorkDetail } from '@/features/work/WorkDetail'
import { WorkList } from '@/features/work/WorkList'
import { ProjectDetail, ProjectList } from '@/features/projects/Projects'

export function App() {
  const auth = useAuth()
  const me = useMe(auth.status === 'signed-in' && auth.organizationId !== null)

  if (auth.status === 'loading') return <Loading label="phiên đăng nhập" />
  if (auth.status === 'error') {
    return <ErrorState error={new Error(auth.error ?? 'Đăng nhập không thành công.')} />
  }
  if (auth.status === 'signed-out') {
    return (
      <main className="gate">
        <div className="gate__card">
          <span className="brand brand--large">
            <span className="brand__mark" aria-hidden="true" />
            AI WorkOS
          </span>
          <p className="gate__lead">
            Hệ thống quản trị công việc. Ghi nhận từ hoạt động thật, không phải từ biểu mẫu.
          </p>
          <button type="button" className="button button--primary" onClick={auth.signIn}>
            Đăng nhập
          </button>
        </div>
      </main>
    )
  }
  // Never inferred, even from a single membership (security-model §2) — so it is asked for.
  if (!auth.organizationId) return <ChooseOrganization />
  if (me.isPending || me.isLoading) return <Loading label="hồ sơ của bạn" />
  if (me.isError) return <ErrorState error={me.error} retry={() => void me.refetch()} />

  const canManageProjects = me.data.roles.some((role) =>
    ['org_admin', 'department_lead', 'team_lead'].includes(role),
  )
  // Writing the policy is `org_admin` and nothing else (ADR-0047). Reading it is organization-wide,
  // but a link that always leads to a read-only screen is not what this one is for.
  const canManagePolicy = me.data.roles.includes('org_admin')

  return (
    <div className="shell">
      <header className="shell__bar">
        <span className="brand">
          <span className="brand__mark" aria-hidden="true" />
          AI WorkOS
        </span>
        {/* Grouped by what a person came to do, not by entity. "Cần xử lý" is where the day starts;
            "Ghi nhận" is how things get in; "Quản trị" is rare and deliberately last. */}
        <nav aria-label="Điều hướng chính">
          <span className="nav__group">
            <NavLink to="/attention">Cần chú ý</NavLink>
            <NavLink to="/proposals">Đề xuất</NavLink>
            <NavLink to="/commitments">Lời hứa</NavLink>
          </span>
          <span className="nav__group">
            <NavLink to="/work">Công việc</NavLink>
            {canManageProjects ? <NavLink to="/projects">Dự án</NavLink> : null}
          </span>
          <span className="nav__group">
            <NavLink to="/work/new">Tạo công việc</NavLink>
            <NavLink to="/capture">Ghi nhận tin nhắn</NavLink>
          </span>
          {/* Administration. Last, and mostly gated: it is the part of the product a person visits
              rarely and deliberately. "Tin nhắn đã nhận" is the exception — anyone may review what
              the system received, because the list already narrows to what they may read. */}
          <span className="nav__group">
            <NavLink to="/messages">Tin nhắn đã nhận</NavLink>
            {canManagePolicy ? <NavLink to="/people">Người dùng</NavLink> : null}
            {canManagePolicy ? <NavLink to="/agent-policy">Quyền của AI</NavLink> : null}
          </span>
        </nav>
        <p className="shell__who">
          <span data-testid="me-name">{me.data.display_name}</span>
          <button type="button" className="button button--quiet" onClick={auth.signOut}>
            Đăng xuất
          </button>
        </p>
      </header>
      <main>
        <Routes>
          {/* A management product opens on what needs a person, not on a backlog. */}
          <Route path="/" element={<Attention />} />
          <Route path="/work" element={<WorkList />} />
          <Route path="/work/new" element={<CreateWork />} />
          <Route path="/work/:workId" element={<WorkDetail />} />
          <Route path="/attention" element={<Attention />} />
          <Route path="/capture" element={<Capture />} />
          <Route path="/messages" element={<MessageList />} />
          <Route path="/messages/:eventId" element={<MessageDetail />} />
          <Route path="/people" element={<People />} />
          <Route path="/proposals" element={<ProposalList />} />
          <Route path="/proposals/:proposalId" element={<ProposalDetail />} />
          <Route path="/commitments" element={<CommitmentList />} />
          <Route path="/commitments/:commitmentId" element={<CommitmentDetail />} />
          <Route path="/projects" element={<ProjectList />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/agent-policy" element={<AgentPolicy />} />
        </Routes>
      </main>
    </div>
  )
}
