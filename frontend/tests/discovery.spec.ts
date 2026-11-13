import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockCurrentUser } from './auth-fixtures'

// Synthetic wire responses are limited to these isolated interaction tests.
const games = [
  {
    steam_app_id: 108600,
    name: 'Project Zomboid',
    similarity_score: 0.7,
    popularity_score: 0.8,
    review_quality: 0.9,
    hybrid_score: 0.745,
  },
  {
    steam_app_id: 892970,
    name: 'Valheim',
    similarity_score: 0.6,
    popularity_score: 0.8,
    review_quality: 0.9,
    hybrid_score: 0.675,
  },
  {
    steam_app_id: 105600,
    name: 'Terraria',
    similarity_score: 0.5,
    popularity_score: 0.8,
    review_quality: 0.9,
    hybrid_score: 0.605,
  },
]
const explanation = {
  steam_app_id: 108600,
  game_name: 'Project Zomboid',
  explanation:
    'Cooperative zombie survival and crafting make this a match for part of your search.',
  matching_features: ['Cooperative play', 'Survival', 'Crafting'],
}

async function mockSearch(page: Page) {
  await page.route('**/api/search/games?**', (route) =>
    route.fulfill({
      json: {
        query: new URL(route.request().url()).searchParams.get('query'),
        total: games.length,
        limit: 20,
        games,
      },
    }),
  )
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
}

test.beforeEach(async ({ page }) => {
  await mockCurrentUser(page, null)
  await mockSearch(page)
})

test('starting screen and example selection do not call search or explanations', async ({
  page,
}) => {
  const calls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/') && !request.url().includes('/auth/')) calls.push(request.url())
  })
  await page.goto('/')
  await expect(page).toHaveURL(/\/discover$/)
  await page.getByRole('button', { name: /A world to get lost in/ }).click()
  await expect(page.getByLabel('What would you like to play?')).toHaveValue(/Open-world RPG/)
  await expect(page.getByRole('heading', { name: 'Try a search' })).toBeVisible()
  expect(calls).toEqual([])
  await expect(page.getByRole('link', { name: /Recommendations/ })).toHaveAttribute(
    'href',
    '/recommendations',
  )
  await expect(page.getByRole('link', { name: 'Library', exact: true })).toHaveAttribute(
    'href',
    '/library',
  )
})

test('Enter submits search, preserves ordering and displays no fabricated ratings', async ({
  page,
}) => {
  await page.goto('/discover')
  await page.getByLabel('What would you like to play?').fill('  survival crafting  ')
  await page.getByLabel('What would you like to play?').press('Enter')
  await expect(page).toHaveURL(/query=survival\+crafting/)
  await expect(page.getByRole('article')).toHaveCount(3)
  await expect(page.locator('.game-title-row h3')).toHaveText(games.map((game) => game.name))
  await expect(page.getByText('3 games returned')).toBeVisible()
  await expect(page.getByText(/0\.745|74\.5%/)).toHaveCount(0)
  await expect(
    page.getByRole('img', { name: 'Project Zomboid: artwork unavailable' }),
  ).toBeVisible()
})

test('filters use repeated labels, inclusive years and explicit limit', async ({ page }) => {
  await page.goto('/discover')
  await page.getByLabel('What would you like to play?').fill('a game for friends')
  await page.getByText('Filters', { exact: true }).click()
  await page.getByLabel('Genres', { exact: true }).fill('Action, RPG')
  await page.getByLabel('Categories', { exact: true }).fill('Co-op, Single-player')
  await page.getByLabel('Released from').fill('2020')
  await page.getByLabel('Released through').fill('2025')
  await page.getByLabel('Minimum reviews').fill('500')
  await page.getByLabel('Result limit').selectOption('50')
  const request = page.waitForRequest('**/api/search/games?**')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  const params = new URL((await request).url()).searchParams
  expect(params.getAll('genres')).toEqual(['Action', 'RPG'])
  expect(params.getAll('categories')).toEqual(['Co-op', 'Single-player'])
  expect(params.get('release_year_from')).toBe('2020')
  expect(params.get('release_year_to')).toBe('2025')
  expect(params.get('min_reviews')).toBe('500')
  expect(params.get('limit')).toBe('50')
  await expect(page.getByText(/at least 100 reviews/)).toBeVisible()
})

test('URL restoration, reload and history preserve submitted search', async ({ page }) => {
  await page.goto('/discover?query=first+query&genres=RPG&categories=Co-op&limit=50')
  await expect(page.getByRole('article')).toHaveCount(3)
  await expect(page.getByLabel('Genres', { exact: true })).toHaveValue('RPG')
  await expect(page.getByLabel('Result limit')).toHaveValue('50')
  await page.getByLabel('What would you like to play?').fill('second query')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page).toHaveURL(/query=second\+query/)
  await page.reload()
  await expect(page.getByLabel('What would you like to play?')).toHaveValue('second query')
  await page.goBack()
  await expect(page.getByLabel('What would you like to play?')).toHaveValue('first query')
})

test('invalid queries and inverted years never call the API', async ({ page }) => {
  let calls = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/') && !request.url().includes('/auth/')) calls++
  })
  await page.goto('/discover')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Describe what')
  await page.getByLabel('What would you like to play?').fill('RPG')
  await page.getByText('Filters', { exact: true }).click()
  await page.getByLabel('Released from').fill('2025')
  await page.getByLabel('Released through').fill('2020')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('earliest release year')
  await page.goto(`/discover?query=${'x'.repeat(501)}`)
  await expect(page.getByRole('alert')).toContainText('500 characters')
  expect(calls).toBe(0)
})

test('empty results have a useful state', async ({ page }) => {
  await page.route('**/api/search/games?**', (route) =>
    route.fulfill({ json: { query: 'nothing', total: 0, limit: 20, games: [] } }),
  )
  await page.goto('/discover?query=nothing')
  await expect(page.getByRole('heading', { name: 'No games found for this search' })).toBeVisible()
  await expect(page.getByText('0 games returned')).toBeVisible()
})

for (const status of [422, 503]) {
  test(`search HTTP ${status} has an explicit retry without automatic requests`, async ({
    page,
  }) => {
    let calls = 0
    await page.route('**/api/search/games?**', (route) => {
      calls++
      return route.fulfill({ status, json: { detail: 'failure' } })
    })
    await page.goto('/discover?query=test')
    await expect(page.getByRole('alert')).toBeVisible()
    expect(calls).toBe(1)
    await page.getByRole('button', { name: 'Try again' }).click()
    await expect.poll(() => calls).toBe(2)
  })
}

test('network failures remain recoverable', async ({ page }) => {
  await page.route('**/api/search/games?**', (route) => route.abort())
  await page.goto('/discover?query=test')
  await expect(page.getByRole('alert')).toContainText('Could not connect')
})

test('card inspection is free of inference; explicit explanation uses submitted query', async ({
  page,
}) => {
  const bodies: unknown[] = []
  await page.route('**/api/explanations/search', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: explanation })
  })
  await page.goto('/discover?query=cooperative+survival')
  await page.getByRole('button', { name: 'Inspect Project Zomboid' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(bodies).toEqual([])
  await page.getByRole('button', { name: 'Close game details' }).click()
  await expect(page.getByRole('button', { name: 'Inspect Project Zomboid' })).toBeFocused()
  await page.getByLabel('What would you like to play?').fill('unsent edited query')
  await page.getByRole('article').first().getByRole('button', { name: 'Why this game?' }).click()
  await expect(page.getByText(explanation.explanation)).toBeVisible()
  await expect(page.getByRole('dialog').getByRole('listitem')).toHaveText(
    explanation.matching_features,
  )
  expect(bodies).toEqual([{ steam_app_id: 108600, search_query: 'cooperative survival' }])
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(
    page.getByRole('article').first().getByRole('button', { name: 'Why this game?' }),
  ).toBeFocused()
})

test('inspection drawer explanation button only submits once while pending', async ({ page }) => {
  let calls = 0
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/explanations/search', async (route) => {
    calls++
    await pending
    await route.fulfill({ json: explanation })
  })
  await page.goto('/discover?query=survival')
  await page.getByRole('button', { name: 'Inspect Project Zomboid' }).click()
  await page.getByRole('button', { name: 'Explain this match' }).click()
  await expect(
    page.getByRole('status', { name: '' }).filter({ hasText: 'Putting the match' }),
  ).toBeVisible()
  expect(calls).toBe(1)
  await expect(page.getByRole('button', { name: 'Explain this match' })).toHaveCount(0)
  release()
  await expect(page.getByText(explanation.explanation)).toBeVisible()
  expect(calls).toBe(1)
})

for (const status of [404, 422, 502, 503, 504]) {
  test(`explanation HTTP ${status} preserves results and permits explicit retry`, async ({
    page,
  }) => {
    let calls = 0
    await page.route('**/api/explanations/search', (route) => {
      calls++
      return route.fulfill({ status, json: { detail: 'failure' } })
    })
    await page.goto('/discover?query=survival')
    await page.getByRole('article').first().getByRole('button', { name: 'Why this game?' }).click()
    await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible()
    expect(calls).toBe(1)
    await page.getByRole('dialog').getByRole('button', { name: 'Try again' }).click()
    await expect.poll(() => calls).toBe(2)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('article')).toHaveCount(3)
  })
}

test('closing a pending explanation cannot leak its output into another game', async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/explanations/search', async (route) => {
    await pending
    await route.fulfill({ json: explanation }).catch(() => {})
  })
  await page.goto('/discover?query=survival')
  await page.getByRole('article').first().getByRole('button', { name: 'Why this game?' }).click()
  await expect(page.getByText('Putting the match into words…')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Inspect Valheim' }).click()
  release()
  await expect(
    page.getByRole('dialog').getByRole('heading', { name: 'Valheim', exact: true }),
  ).toBeVisible()
  await expect(page.getByText(explanation.explanation)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Explain this match' })).toBeVisible()
})

test('obsolete search responses cannot replace the latest query', async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/search/games?**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('query') === 'old') {
      await pending
      await route
        .fulfill({ json: { query: 'old', total: 0, limit: 20, games: [] } })
        .catch(() => {})
    } else {
      await route.fulfill({ json: { query: 'new', total: 3, limit: 20, games } })
    }
  })
  await page.goto('/discover?query=old')
  await expect(page.getByRole('status', { name: 'Searching games' })).toBeVisible()
  await page.evaluate(() => {
    window.history.pushState(null, '', '/discover?query=new')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page.getByRole('article')).toHaveCount(3)
  release()
  await expect(page.getByLabel('What would you like to play?')).toHaveValue('new')
  await expect(page.getByRole('article')).toHaveCount(3)
})

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
  { width: 320, height: 740 },
]) {
  test(`layout, focus and reduced motion at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/discover?query=survival')
    await expect(page.getByRole('article')).toHaveCount(3)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.getByRole('button', { name: 'Inspect Project Zomboid' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(page.getByRole('button', { name: 'Close game details' })).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true)
    expect(await dialog.evaluate((node) => getComputedStyle(node).animationName)).toBe('none')
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Inspect Project Zomboid' })).toBeFocused()
  })
}

test('long titles wrap without horizontal overflow', async ({ page }) => {
  const name = 'AnExtraordinarilyLongUnbrokenGameTitle'.repeat(8)
  await page.route('**/api/search/games?**', (route) =>
    route.fulfill({ json: { query: 'long', total: 1, limit: 20, games: [{ ...games[0], name }] } }),
  )
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/discover?query=long')
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.getByRole('button', { name: `Inspect ${name}` }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
})
