// Eval results, summarised the way src/stagecraft/evals/report.py does it, so the console and
// the markdown report agree: pass rate excludes errors; a case passes a run set when every
// measured run passed; flaky means some runs passed and some did not.

import type { CaseResult, SuiteResult } from '@/lib/types'

export const CATEGORIES = ['routing', 'tool_calls', 'completion', 'robustness'] as const

export interface Tally {
  runs: number
  passed: number
  failed: number
  errors: number
}

export function rate(tally: Tally): number {
  const measured = tally.runs - tally.errors
  return measured ? tally.passed / measured : 0
}

/** Per category in the fixed order, then any other category seen, then ``all``. */
export function tallies(results: CaseResult[]): [string, Tally][] {
  const table = new Map<string, Tally>(CATEGORIES.map((c) => [c, empty()]))
  const overall = empty()
  for (const result of results) {
    if (!table.has(result.category)) table.set(result.category, empty())
    count(table.get(result.category)!, result)
    count(overall, result)
  }
  return [...table.entries(), ['all', overall]]
}

function empty(): Tally {
  return { runs: 0, passed: 0, failed: 0, errors: 0 }
}

function count(tally: Tally, result: CaseResult): void {
  tally.runs += 1
  if (result.status === 'passed') tally.passed += 1
  else if (result.status === 'failed') tally.failed += 1
  else tally.errors += 1
}

export interface FlakyCase {
  caseId: string
  passed: number
  runs: number
}

export function flakyCases(results: CaseResult[]): FlakyCase[] {
  const outcomes = new Map<string, string[]>()
  for (const result of results) {
    outcomes.set(result.case_id, [...(outcomes.get(result.case_id) ?? []), result.status])
  }
  return [...outcomes.entries()]
    .map(([caseId, statuses]) => ({
      caseId,
      passed: statuses.filter((s) => s === 'passed').length,
      runs: statuses.length,
    }))
    .filter((c) => c.passed > 0 && c.passed < c.runs)
    .sort((a, b) => a.caseId.localeCompare(b.caseId))
}

export function judgeMeans(results: CaseResult[]): { criterion: string; mean: number }[] {
  const scores = new Map<string, number[]>()
  for (const result of results) {
    for (const [criterion, score] of Object.entries(result.judge?.scores ?? {})) {
      scores.set(criterion, [...(scores.get(criterion) ?? []), score])
    }
  }
  return [...scores.entries()].map(([criterion, values]) => ({
    criterion,
    mean: values.reduce((sum, value) => sum + value, 0) / values.length,
  }))
}

/** Per case: true when every measured run passed, false when one failed, null when all errored. */
function passing(results: CaseResult[]): Map<string, boolean | null> {
  const seen = new Map<string, string[]>()
  for (const result of results) {
    seen.set(result.case_id, [...(seen.get(result.case_id) ?? []), result.status])
  }
  return new Map(
    [...seen.entries()].map(([caseId, statuses]) => {
      const measured = statuses.filter((s) => s !== 'error')
      return [caseId, measured.length ? measured.every((s) => s === 'passed') : null]
    }),
  )
}

export interface Comparison {
  categories: { name: string; before: Tally; after: Tally; delta: number }[]
  nowPassing: string[]
  nowFailing: string[]
}

export function compare(before: SuiteResult, after: SuiteResult): Comparison {
  const old = new Map(tallies(before.results))
  const categories = tallies(after.results).map(([name, tally]) => {
    const previous = old.get(name) ?? empty()
    return { name, before: previous, after: tally, delta: (rate(tally) - rate(previous)) * 100 }
  })
  const was = passing(before.results)
  const now = passing(after.results)
  const nowPassing = [...now.entries()]
    .filter(([caseId, ok]) => ok === true && was.get(caseId) === false)
    .map(([caseId]) => caseId)
    .sort()
  const nowFailing = [...now.entries()]
    .filter(([caseId, ok]) => ok === false && was.get(caseId) === true)
    .map(([caseId]) => caseId)
    .sort()
  return { categories, nowPassing, nowFailing }
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}
