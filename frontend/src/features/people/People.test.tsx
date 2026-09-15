/**
 * Administration, and the one thing it must not get wrong.
 *
 * The role gate here picks what to render and decides nothing: the server re-decides every request
 * (contract §14). So the test that matters is not "the button is hidden" but "a person with no
 * roles is shown as having none" — because a colleague who can sign in and do nothing is the state
 * this screen exists to find and fix, and it is invisible everywhere else in the product.
 */
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { People } from './People'
import { renderSurface, stubApi } from '@/test/harness'

const ORG = '11111111-1111-4111-8111-111111111111'
const MAI = '44444444-4444-4444-8444-444444444444'
const ASSIGNMENT = '66666666-6666-4666-8666-666666666666'

const me = (roles: string[]) => ({
  match: 'GET /api/v1/me',
  body: { person_id: MAI, org_id: ORG, display_name: 'Avery Admin', email: null, roles },
})

const people = {
  match: 'GET /api/v1/people',
  body: {
    items: [
      {
        id: MAI,
        org_id: ORG,
        display_name: 'Mai Tran',
        email: 'mai@example.test',
        status: 'active',
        timezone: 'Asia/Ho_Chi_Minh',
        created_at: '2026-09-01T00:00:00Z',
        version: 1,
      },
    ],
    next_cursor: null,
  },
}

const roles = (items: unknown[]) => ({
  match: `GET /api/v1/people/${MAI}/roles`,
  body: { items, next_cursor: null },
})

describe('People', () => {
  it('lists who is in the organization', async () => {
    stubApi([me(['org_admin']), people, roles([])])
    renderSurface(<People />)

    expect(await screen.findByTestId('people')).toHaveTextContent('Mai Tran')
  })

  it('says plainly when somebody has no role at all', async () => {
    // A person who can sign in and do nothing. Invisible everywhere else in the product.
    stubApi([me(['org_admin']), people, roles([])])
    renderSurface(<People />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mai Tran' }))

    expect(await screen.findByTestId('no-roles')).toBeVisible()
  })

  it('names roles in words rather than in identifiers', async () => {
    stubApi([
      me(['org_admin']),
      people,
      roles([
        { id: ASSIGNMENT, org_id: ORG, person_id: MAI, role: 'team_lead', scope_type: 'organization', scope_id: null, version: 1 },
      ]),
    ])
    renderSurface(<People />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mai Tran' }))

    // Scoped to the list of held roles: "Trưởng nhóm" also appears as an option in the grant
    // dropdown below, and matching the page would pass whether or not the role was rendered.
    const panel = await screen.findByTestId(`person-${MAI}`)
    expect(panel.querySelector('.rows')).toHaveTextContent('Trưởng nhóm')
  })

  it('offers no way to change anything without the authority to', async () => {
    stubApi([me(['member']), people, roles([])])
    renderSurface(<People />)

    expect(await screen.findByTestId('read-only')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Cấp vai trò' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Thêm người' })).toBeNull()
  })
})
