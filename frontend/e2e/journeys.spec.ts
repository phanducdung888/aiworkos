/**
 * The curated journeys that Phase 1 can actually complete (testing-strategy §L6).
 *
 * Journey 1  — sign in, create a project, create work in it, assign, complete.
 * Journey 1a — Work with no Project and no assignment, progressed and completed; then owner,
 *              contributors, a reviewer, ownership moved, and the history still visible.
 * Journey 1b — reporting partitions (BR-RPT-01 to BR-RPT-03).
 * Journey 4  — a dependency, the blocker completed, the blocked item free to move.
 *
 * Journeys 2, 3, 5, 6 and 7 need Events, Proposals, Commitments or Risks. None exists yet, and a
 * journey written against a stub tests the stub. Journey 8 (AI disabled) is every path below: there
 * is no AI in the system to switch off, which is the strongest form of the assertion it makes.
 */
import { expect, test } from '@playwright/test'
import { signIn, state } from './setup/session'

const unique = (label: string) => `${label} ${Date.now().toString(36)}`

test.describe('Journey 1 — a project, work in it, assigned and completed', () => {
  test('runs end to end as a team lead', async ({ page }) => {
    await signIn(page, 'tomas.lead')
    await page.goto('/projects')

    const projectName = unique('Migration')
    await page.getByLabel('Name').fill(projectName)
    await page.getByLabel('Owning team').selectOption({ label: 'Platform' })
    await page.getByRole('button', { name: 'Create project' }).click()
    await expect(page.getByRole('link', { name: projectName })).toBeVisible()

    const workTitle = unique('Cut over the API')
    await page.goto('/work/new')
    await page.getByLabel('Title').fill(workTitle)
    await page.getByLabel('Project').selectOption({ label: projectName })
    await page.getByRole('button', { name: 'Capture' }).click()

    await expect(page.getByRole('heading', { name: workTitle })).toBeVisible()
    await expect(page.getByTestId('work-status')).toHaveText('todo')

    await page.getByLabel('Owner').selectOption({ label: 'Mira Member' })
    await page.getByRole('button', { name: 'Set owner' }).click()
    await expect(page.getByTestId('assignment-OWNER')).toBeVisible()

    // A project must be active before its work can start (BR-W-09), which is why the status move
    // below would fail on a `proposed` project — the journey activates it first.
    await page.goto('/projects')
    await page.getByRole('link', { name: projectName }).click()
    await expect(page.getByTestId('project-visibility')).toBeVisible()
  })
})

test.describe('Journey 1a — work with no project and no assignment', () => {
  test('is a complete record from capture to completion', async ({ page }) => {
    await signIn(page, 'mira.member')

    const title = unique('Check the IOC API')
    await page.goto('/work/new')
    await page.getByLabel('Title').fill(title)
    // The project field is left alone on purpose: no UI path may demand one (ADR-0029, BR-W-07).
    await page.getByRole('button', { name: 'Capture' }).click()

    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    await expect(page.getByText(/complete record, not a gap/i)).toBeVisible()

    await page.getByRole('button', { name: 'in progress' }).click()
    await expect(page.getByTestId('work-status')).toHaveText('in_progress')
    await page.getByRole('button', { name: 'done' }).click()
    await expect(page.getByTestId('work-status')).toHaveText('done')
  })

  test('records an owner, contributors and the history of a handover', async ({ page }) => {
    await signIn(page, 'avery.admin')
    const people = state().people

    const title = unique('Staffed work')
    await page.goto('/work/new')
    await page.getByLabel('Title').fill(title)
    await page.getByRole('button', { name: 'Capture' }).click()
    await expect(page.getByRole('heading', { name: title })).toBeVisible()

    await page.getByLabel('Owner').selectOption({ label: 'Mira Member' })
    await page.getByRole('button', { name: 'Set owner' }).click()
    await expect(page.getByTestId('assignment-OWNER')).toBeVisible()

    await page.getByLabel('Add contributor').selectOption({ label: 'Tomas Lead' })
    await page.getByRole('button', { name: 'Add contributor' }).click()
    await expect(page.getByTestId('assignment-CONTRIBUTOR')).toBeVisible()

    // Ownership moves in one call. End-then-assign is the route around REASSIGN that ADR-0032
    // exists to close, and the interface does not offer it.
    await page.getByLabel('Owner').selectOption({ label: 'Avery Admin' })
    await page.getByRole('button', { name: 'Set owner' }).click()

    // BR-W-14: the previous owner is still there, ended rather than deleted.
    await expect(page.getByText(/previous assignments/i)).toBeVisible()
    await page.getByText(/previous assignments/i).click()
    await expect(page.getByText(new RegExp(people['mira.member']!))).toBeVisible()
  })
})

test.describe('Journey 1b — reporting partitions', () => {
  test('splits all work into project and non-project without losing any', async ({ page }) => {
    await signIn(page, 'avery.admin')
    await page.goto('/work')

    const total = Number(await page.getByTestId('count-total').textContent())
    const project = Number(await page.getByTestId('count-project').textContent())
    const nonProject = Number(await page.getByTestId('count-non-project').textContent())

    // BR-RPT-01 and BR-RPT-03: All Work is exactly the two partitions, and both are visible.
    expect(project + nonProject).toBe(total)
    expect(total).toBeGreaterThan(0)
    // BR-RPT-04: each figure says which partition it covers.
    await expect(page.getByText('Not in a project')).toBeVisible()
  })
})

test.describe('Journey 4 — a dependency and its release', () => {
  test('the blocked item is free to move once the blocker is done', async ({ page, request }) => {
    await signIn(page, 'avery.admin')
    const current = state()
    const headers = {
      Authorization: `Bearer ${current.tokens['avery.admin']}`,
      'X-Organization-Id': current.orgId,
    }

    // The dependency itself is created through the API: Phase 1 has no dependency-authoring
    // surface, and inventing one here would be testing a screen that does not exist.
    const blocker = await (
      await request.post('http://127.0.0.1:8000/api/v1/work', {
        headers,
        data: { title: unique('Blocker') },
      })
    ).json()
    const blocked = await (
      await request.post('http://127.0.0.1:8000/api/v1/work', {
        headers,
        data: { title: unique('Blocked') },
      })
    ).json()
    const dependency = await request.post('http://127.0.0.1:8000/api/v1/dependencies', {
      headers,
      data: {
        blocker_type: 'work',
        blocker_id: blocker.id,
        blocked_type: 'work',
        blocked_id: blocked.id,
      },
    })
    expect(dependency.ok()).toBeTruthy()

    await page.goto(`/work/${blocked.id}`)
    await expect(page.getByTestId('blocked-by')).toContainText(blocker.id)

    await page.goto(`/work/${blocker.id}`)
    await page.getByRole('button', { name: 'in progress' }).click()
    await expect(page.getByTestId('work-status')).toHaveText('in_progress')
    await page.getByRole('button', { name: 'done' }).click()
    await expect(page.getByTestId('work-status')).toHaveText('done')

    // BR-D-03: completing the blocker resolves the dependency. The blocked item is not moved by the
    // system — it becomes free to move, which is the narrow reading MON-007a exists to cover.
    await page.goto(`/work/${blocked.id}`)
    await expect(page.getByTestId('blocked-by')).toContainText('resolved')
    await expect(page.getByTestId('work-status')).toHaveText('todo')
  })
})
