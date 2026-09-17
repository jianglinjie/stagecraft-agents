import { describe, expect, it } from 'vitest'

import { compare, flakyCases, judgeMeans, rate, tallies } from '@/lib/evals'
import type { CaseResult, CaseStatus, SuiteResult } from '@/lib/types'

function result(caseId: string, status: CaseStatus, category = 'routing', scores?: Record<string, number>): CaseResult {
  return {
    case_id: caseId,
    category,
    description: '',
    attempt: 1,
    status,
    checks: [],
    judge: scores ? { rubric: 'r', scores, rationale: '', passed: true } : null,
    error: null,
    retried: false,
    turns: [],
    usage: { requests: 0, input_tokens: 0, output_tokens: 0 },
    seconds: 0,
  }
}

function suite(results: CaseResult[]): SuiteResult {
  return { meta: {} as SuiteResult['meta'], results }
}

describe('tallies', () => {
  it('keeps the category order, adds unknown ones, and leaves errors out of the rate', () => {
    const table = tallies([
      result('a', 'passed'),
      result('b', 'error'),
      result('c', 'failed', 'completion'),
      result('d', 'passed', 'custom'),
    ])

    expect(table.map(([name]) => name)).toEqual(['routing', 'tool_calls', 'completion', 'robustness', 'custom', 'all'])
    const routing = table[0][1]
    expect(routing).toEqual({ runs: 2, passed: 1, failed: 0, errors: 1 })
    expect(rate(routing)).toBe(1)
    expect(rate(table.at(-1)![1])).toBeCloseTo(2 / 3)
  })
})

describe('flaky cases and judge means', () => {
  it('flags mixed outcomes only', () => {
    const results = [result('a', 'passed'), result('a', 'failed'), result('b', 'passed'), result('b', 'passed')]
    expect(flakyCases(results)).toEqual([{ caseId: 'a', passed: 1, runs: 2 }])
  })

  it('averages each criterion over judged runs', () => {
    const results = [result('a', 'passed', 'routing', { concise: 4 }), result('b', 'passed', 'routing', { concise: 5 }), result('c', 'failed')]
    expect(judgeMeans(results)).toEqual([{ criterion: 'concise', mean: 4.5 }])
  })
})

describe('compare', () => {
  it('reports rate deltas and cases whose verdict flipped, ignoring all-error cases', () => {
    const before = suite([result('fixed', 'failed'), result('broke', 'passed'), result('noise', 'error'), result('same', 'passed')])
    const after = suite([result('fixed', 'passed'), result('broke', 'failed'), result('noise', 'passed'), result('same', 'passed')])

    const comparison = compare(before, after)

    expect(comparison.nowPassing).toEqual(['fixed'])
    expect(comparison.nowFailing).toEqual(['broke'])
    const routing = comparison.categories.find((c) => c.name === 'routing')!
    expect(routing.delta).toBeCloseTo((3 / 4 - 2 / 3) * 100)
  })
})
