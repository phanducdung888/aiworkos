/**
 * Naming the organization (CP25).
 *
 * Until CP25 a successful sign-in ended on the sentence "Choose the organization you are working
 * in." with nothing to choose with: `setOrganizationId` existed and no component called it. The
 * app made no API call at all, which reads as a broken product rather than as a missing step.
 *
 * The identifier is checked before it is stored. An identifier that is not yours 404s every
 * subsequent request, and an app that looks empty is much harder to diagnose than one that says
 * why.
 */
import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChooseOrganization } from '@/auth/ChooseOrganization'
import { renderSurface, stubApi } from '@/test/harness'

const ORG = '01a093df-3607-707d-8342-f155ece35b0a'

const setOrganizationId = vi.fn()
const signOut = vi.fn()

vi.mock('@/auth/AuthProvider', () => ({
  useAuth: () => ({
    status: 'signed-in',
    error: null,
    organizationId: null,
    setOrganizationId,
    signIn: vi.fn(),
    signOut,
  }),
}))

const me = {
  person_id: '55555555-5555-4555-8555-555555555555',
  org_id: ORG,
  display_name: 'Avery Admin',
  email: 'avery.admin@example.test',
  roles: ['org_admin'],
}

describe('ChooseOrganization', () => {
  it('checks the identifier against the API before committing to it', async () => {
    let sentOrganization: string | null = null
    stubApi([
      {
        match: 'GET /api/v1/me',
        body: me,
        onRequest: (request) => {
          sentOrganization = request.headers.get('X-Organization-Id')
        },
      },
    ])
    renderSurface(<ChooseOrganization />)

    await userEvent.type(screen.getByLabelText(/mã tổ chức/i), ORG)
    await userEvent.click(screen.getByRole('button', { name: /tiếp tục/i }))

    expect(await screen.findByRole('button', { name: /tiếp tục/i })).toBeEnabled()
    // The identifier being tried, not the one the harness configures globally — the whole point
    // is that it is not committed to anything until it is known to work.
    expect(sentOrganization).toBe(ORG)
    expect(setOrganizationId).toHaveBeenCalledWith(ORG)
  })

  it('says what is wrong when the organization is not yours, and stores nothing', async () => {
    setOrganizationId.mockClear()
    stubApi([
      {
        match: 'GET /api/v1/me',
        status: 404,
        body: { status: 404, title: 'Not found', detail: 'No such organization.' },
      },
    ])
    renderSurface(<ChooseOrganization />)

    await userEvent.type(screen.getByLabelText(/mã tổ chức/i), ORG)
    await userEvent.click(screen.getByRole('button', { name: /tiếp tục/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/thành viên đang hoạt động/i)
    expect(setOrganizationId).not.toHaveBeenCalled()
  })

  it('distinguishes a member with no role from a stranger', async () => {
    setOrganizationId.mockClear()
    stubApi([
      {
        match: 'GET /api/v1/me',
        status: 403,
        body: { status: 403, title: 'Forbidden', detail: 'No roles held.' },
      },
    ])
    renderSurface(<ChooseOrganization />)

    await userEvent.type(screen.getByLabelText(/mã tổ chức/i), ORG)
    await userEvent.click(screen.getByRole('button', { name: /tiếp tục/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/chưa được giao vai trò/i)
    expect(setOrganizationId).not.toHaveBeenCalled()
  })

  it('offers a way out, so a wrong account is not a dead end', async () => {
    stubApi([])
    renderSurface(<ChooseOrganization />)
    await userEvent.click(screen.getByRole('button', { name: /đăng xuất/i }))
    expect(signOut).toHaveBeenCalled()
  })
})
