import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { RecommendationRequest } from '../src/api/types'

import { USER_A, USER_B, game } from './recommendation-fixtures'

async function apply(page: Page, steamId: string, limit = '20') {
  await page.getByLabel('Test Steam ID').fill(steamId)
  await page.getByLabel('Test limit').fill(limit)
  await page.getByRole('button', { name: 'Apply test inputs' }).click()
}

test('absent and whitespace IDs disable the query', async ({ page }) => {
  let calls = 0
  await page.route('**/api/recommendations', (route) => {
    calls++
    return route.abort()
  })
  await page.goto('/tests/recommendations.html')
  await expect(page.getByTestId('state')).toHaveText('disabled')
  await apply(page, '   ')
  await expect(page.getByTestId('state')).toHaveText('disabled')
  expect(calls).toBe(0)
})

test('typed client sends a JSON string ID with defaults and optional limit', async ({ page }) => {
  const bodies: RecommendationRequest[] = []
  await page.route('**/api/recommendations', (route) => {
    expect(route.request().method()).toBe('POST')
    expect(route.request().headers()['content-type']).toBe('application/json')
    expect(new URL(route.request().url()).search).toBe('')
    const body = route.request().postDataJSON() as RecommendationRequest
    bodies.push(body)
    return route.fulfill({ json: { total: 1, limit: body.limit, games: [game] } })
  })
  await page.goto('/tests/recommendations.html')
  const result = await page.evaluate(async (id) => {
    const moduleUrl = '/src/api/client.ts'
    const api = (await import(/* @vite-ignore */ moduleUrl)) as typeof import('../src/api/client')
    const first = await api.getRecommendations(`  ${id}  `)
    const second = await api.getRecommendations(id, { limit: 50 })
    return { first, second }
  }, USER_A)
  expect(result.first).toEqual({ total: 1, limit: 20, games: [game] })
  expect(result.second.limit).toBe(50)
  expect(bodies).toEqual([
    { steamid64: USER_A, limit: 20 },
    { steamid64: USER_A, limit: 50 },
  ])
})

test('hook exposes loading then success with nullable names and no invented metadata', async ({
  page,
}) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/recommendations', async (route) => {
    await pending
    await route.fulfill({ json: { total: 1, limit: 20, games: [{ ...game, name: null }] } })
  })
  await page.goto('/tests/recommendations.html')
  await apply(page, USER_A)
  await expect(page.getByTestId('state')).toHaveText('loading')
  release()
  await expect(page.getByTestId('state')).toHaveText('success')
  await expect(page.getByTestId('count')).toHaveText('1')
  await expect(page.getByRole('listitem')).toHaveText('Steam app 20')
})

test('empty recommendations are a successful empty response', async ({ page }) => {
  await page.route('**/api/recommendations', (route) =>
    route.fulfill({ json: { total: 0, limit: 20, games: [] } }),
  )
  await page.goto('/tests/recommendations.html')
  await apply(page, USER_A)
  await expect(page.getByTestId('state')).toHaveText('success')
  await expect(page.getByTestId('count')).toHaveText('0')
  await expect(page.getByRole('listitem')).toHaveCount(0)
})

test('keys normalize defaults, preserve strings and include each result parameter', async ({
  page,
}) => {
  await page.goto('/tests/recommendations.html')
  const keys = await page.evaluate(
    async ({ first, second }) => {
      const moduleUrl = '/src/features/recommendations/useRecommendations.ts'
      const { recommendationQueryKey } = (await import(
        /* @vite-ignore */ moduleUrl
      )) as typeof import('../src/features/recommendations/useRecommendations')
      return [
        recommendationQueryKey(` ${first} `),
        recommendationQueryKey(first, { limit: 20 }),
        recommendationQueryKey(first, { limit: 50 }),
        recommendationQueryKey(second),
        recommendationQueryKey(undefined),
        recommendationQueryKey(null),
      ]
    },
    { first: USER_A, second: USER_B },
  )
  expect(keys[0]).toEqual(['recommendations', USER_A, { limit: 20 }])
  expect(keys[0]).toEqual(keys[1])
  expect(keys[0]).not.toEqual(keys[2])
  expect(keys[0]).not.toEqual(keys[3])
  expect(keys[4]).toEqual(keys[5])
})

test('hook isolates IDs and limits while reusing fresh same-key responses', async ({ page }) => {
  const bodies: RecommendationRequest[] = []
  await page.route('**/api/recommendations', (route) => {
    const body = route.request().postDataJSON() as RecommendationRequest
    bodies.push(body)
    return route.fulfill({
      json: {
        total: 1,
        limit: body.limit,
        games: [
          {
            ...game,
            name: body.steamid64 === USER_A ? `First user, limit ${body.limit}` : 'Second user',
          },
        ],
      },
    })
  })
  await page.goto('/tests/recommendations.html')
  await apply(page, USER_A)
  await expect(page.getByRole('listitem')).toHaveText('First user, limit 20')
  await apply(page, ` ${USER_A} `)
  await expect(page.getByTestId('state')).toHaveText('success')
  expect(bodies).toHaveLength(1)
  await apply(page, USER_A, '50')
  await expect(page.getByRole('listitem')).toHaveText('First user, limit 50')
  await apply(page, USER_B)
  await expect(page.getByRole('listitem')).toHaveText('Second user')
  await apply(page, USER_A)
  await expect(page.getByRole('listitem')).toHaveText('First user, limit 20')
  expect(bodies).toEqual([
    { steamid64: USER_A, limit: 20 },
    { steamid64: USER_A, limit: 50 },
    { steamid64: USER_B, limit: 20 },
  ])
})

for (const status of [404, 422, 503]) {
  test(`HTTP ${status} is exposed as a recommendation error without retries`, async ({ page }) => {
    let calls = 0
    await page.route('**/api/recommendations', (route) => {
      calls++
      return route.fulfill({ status, json: { detail: 'synthetic error' } })
    })
    await page.goto('/tests/recommendations.html')
    await apply(page, status === 422 ? 'not-an-id' : USER_A)
    await expect(page.getByTestId('state')).toHaveText('error')
    const message =
      status === 404
        ? 'No stored user'
        : status === 422
          ? 'Check the Steam ID'
          : 'temporarily unavailable'
    await expect(page.getByRole('alert')).toContainText(message)
    expect(calls).toBe(1)
  })
}

test('network failure remains an error, never fake recommendations', async ({ page }) => {
  await page.route('**/api/recommendations', (route) => route.abort())
  await page.goto('/tests/recommendations.html')
  await apply(page, USER_A)
  await expect(page.getByRole('alert')).toContainText('Could not connect')
  await expect(page.getByRole('listitem')).toHaveCount(0)
})

test('typed client forwards cancellation as AbortError', async ({ page }) => {
  await page.route('**/api/recommendations', (route) => route.abort('aborted'))
  await page.goto('/tests/recommendations.html')
  const cancelled = await page.evaluate(async (id) => {
    const moduleUrl = '/src/api/client.ts'
    const { getRecommendations } = (await import(
      /* @vite-ignore */ moduleUrl
    )) as typeof import('../src/api/client')
    const controller = new AbortController()
    controller.abort()
    try {
      await getRecommendations(id, {}, controller.signal)
      return false
    } catch (error) {
      return error instanceof DOMException && error.name === 'AbortError'
    }
  }, USER_A)
  expect(cancelled).toBe(true)
})

test('obsolete hook responses cannot replace a different account', async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/recommendations', async (route) => {
    const body = route.request().postDataJSON() as RecommendationRequest
    if (body.steamid64 === USER_A) {
      await pending
      await route.fulfill({ json: { total: 1, limit: 20, games: [game] } }).catch(() => {})
    } else {
      await route.fulfill({ json: { total: 0, limit: 20, games: [] } })
    }
  })
  await page.goto('/tests/recommendations.html')
  await apply(page, USER_A)
  await expect(page.getByTestId('state')).toHaveText('loading')
  await apply(page, USER_B)
  await expect(page.getByTestId('state')).toHaveText('success')
  release()
  await expect(page.getByTestId('count')).toHaveText('0')
  await expect(page.getByRole('listitem')).toHaveCount(0)
})
