import test from 'node:test'
import assert from 'node:assert/strict'
import { parseCny, summarizeBudgetReview } from '../src/utils/tripBudgetReview.js'

test('原估算不进入报价小计，未报价类别不会默认填零', () => {
  const trip = { days: [{}, {}, {}], budget_review: { reference: { limit_minor: 300000, subtotal_minor: 142000 } } }
  const result = summarizeBudgetReview(trip)
  assert.deepEqual(result, { limit: 300000, subtotal: 0, remaining: 6, quoted: 0, entries: {}, state: 'pending' })
})

test('部分报价超额成立，但低于上限、全部不计入或行程变更不能提前报达标', () => {
  const scope = { start_date: '2026-11-21', destination: '杭州', days: 3 }
  const trip = { start_date: scope.start_date, destination: scope.destination, days: [{}, {}, {}],
    budget_review: { checklist_scope: scope, checklist: { limit_minor: 300000, entries: {
      lodging: { status: 'quoted', amount_minor: 301201 }, admission: { status: 'quoted', amount_minor: 0 },
    } } } }
  assert.equal(summarizeBudgetReview(trip).state, 'over')
  trip.budget_review.checklist.entries.lodging.amount_minor = 100000
  assert.equal(summarizeBudgetReview(trip).state, 'pending')
  for (const key of ['local_transport', 'intercity_transport', 'food', 'other']) {
    trip.budget_review.checklist.entries[key] = { status: 'excluded' }
  }
  assert.equal(summarizeBudgetReview(trip).state, 'within')
  assert.equal(summarizeBudgetReview({ ...trip, start_date: '2026-12-01' }).state, 'stale')
  for (const row of Object.values(trip.budget_review.checklist.entries)) row.status = 'excluded'
  assert.equal(summarizeBudgetReview(trip).state, 'no_quotes')
  assert.equal(parseCny('0.00'), 0)
  assert.equal(parseCny('30.07'), 3007)
  assert.throws(() => parseCny('1.001'))
})
