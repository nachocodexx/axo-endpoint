import type { ChartAnswer } from '../types/cp'
import type { TimeSeries } from '../components/AxoTimeChart.vue'

// Turns /stats/chart answers into AxoTimeChart series. A missing step is a
// gap (null) for values, 0 for counts (`fill`), so bars don't skip.

/** Every step of the window, aligned like the server's points. */
export function chartSteps(answer: ChartAnswer): number[] {
  const step = answer.step_s
  const first = Math.floor(answer.start / step) * step
  const out: number[] = []
  for (let t = first; t < answer.end; t += step) out.push(t)
  return out
}

/** One series per group (or one for an ungrouped chart) of one statistic. */
export function chartSeries(answer: ChartAnswer | null | undefined, stat: string, opts: {
  name?: (group: string | null) => string
  color?: (group: string | null) => string | undefined
  fill?: number | null
  type?: 'line' | 'bar'
  stack?: string
  order?: string[]
} = {}): TimeSeries[] {
  if (!answer) return []
  const byGroup = new Map<string | null, Map<number, number | null>>()
  for (const p of answer.points) {
    if (!byGroup.has(p.group)) byGroup.set(p.group, new Map())
    byGroup.get(p.group)!.set(p.t, p.values[stat] ?? null)
  }
  const steps = chartSteps(answer)
  const groups = [...byGroup.keys()].sort((a, b) => rank(a, opts.order) - rank(b, opts.order))
  return groups.map(group => ({
    name: opts.name?.(group) ?? group ?? stat,
    color: opts.color?.(group),
    type: opts.type,
    stack: opts.stack,
    points: steps.map(t => [t, byGroup.get(group)!.get(t) ?? opts.fill ?? null] as [number, number | null]),
  }))
}

/** One statistic added up over the groups, per step (e.g. all jobs). */
export function chartTotals(answer: ChartAnswer | null | undefined, stat: string): number[] {
  if (!answer) return []
  const sums = new Map<number, number>()
  for (const p of answer.points) sums.set(p.t, (sums.get(p.t) ?? 0) + (p.values[stat] ?? 0))
  return chartSteps(answer).map(t => sums.get(t) ?? 0)
}

function rank(group: string | null, order?: string[]): number {
  const i = order ? order.indexOf(group ?? '') : -1
  return i < 0 ? 999 : i
}
