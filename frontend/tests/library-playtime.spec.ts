import { expect, test } from '@playwright/test'
import { formatPlaytime } from '../src/features/library/playtime'

test('playtime formatting distinguishes unknown, zero, minutes and rounded hours', () => {
  expect([null, undefined, 0, 1, 45, 59, 60, 90, 1200, 1250].map(formatPlaytime)).toEqual([
    'Playtime unavailable',
    'Playtime unavailable',
    'Not played',
    '1 min',
    '45 min',
    '59 min',
    '1 hr',
    '1.5 hrs',
    '20 hrs',
    '20.8 hrs',
  ])
})
