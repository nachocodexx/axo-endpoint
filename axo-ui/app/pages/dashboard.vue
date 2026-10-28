<template>
  <div>
    <AxoPageHeader title="Dashboard" icon="mdi-view-dashboard-outline"
                   :subtitle="ves.length ? `Hello ${me.displayName || ''} — your jobs across ${scopeLabel}` : `Hello ${me.displayName || ''}`">
      <template #actions>
        <v-select v-if="ves.length > 1" v-model="selected" :items="veItems" multiple chips closable-chips
                  density="compact" variant="outlined" hide-details label="VEs" placeholder="All my VEs"
                  class="axo-dash__ves">
          <template v-if="teamsWithVes.length" #prepend-item>
            <div class="px-4 pt-2 pb-1 text-caption axo-muted">A team's VEs</div>
            <div class="d-flex flex-wrap ga-1 px-3 pb-2">
              <v-chip v-for="t in teamsWithVes" :key="t.name" size="small" variant="tonal" prepend-icon="mdi-account-group-outline"
                      @click="selected = t.ve_ids">{{ t.name }} · {{ t.ve_ids.length }}</v-chip>
            </div>
            <v-divider />
          </template>
        </v-select>
        <AxoRangePicker v-if="ves.length" v-model="range" />
      </template>
    </AxoPageHeader>

    <AxoErrorAlert v-if="error" :error="error" title="The dashboard couldn't load" :retry="load" class="mb-4" />

    <AxoEmptyState v-if="loaded && !ves.length" icon="mdi-cube-outline" title="No VEs yet"
                   text="A Virtual Environment (VE) is where your functions run. Once you buy one, or someone shares theirs with you, its jobs, times and costs show up here." />

    <template v-else>
      <!-- Key figures -->
      <v-row density="compact" class="mb-2">
        <v-col cols="12" sm="6" lg="3">
          <AxoStatCard label="Jobs" icon="mdi-play-circle-outline" :loading="loading" :value="formatNumber(jobs)"
                       :change="percentChange(jobs, jobsBefore)" :change-label="`vs the previous ${rangeTitle}`"
                       :trend="jobTrend" help="Job attempts that finished in this period, in every VE shown." />
        </v-col>
        <v-col cols="12" sm="6" lg="3">
          <AxoStatCard label="Success rate" icon="mdi-check-decagram-outline" color="success" :loading="loading"
                       :value="successRate == null ? null : successRate.toFixed(1)" unit="%"
                       :change="successRate != null && successBefore != null ? successRate - successBefore : null"
                       change-label="Points vs the previous period"
                       help="Share of jobs that completed. Function errors, platform errors and jobs that waited too long count as not completed." />
        </v-col>
        <v-col cols="12" sm="6" lg="3">
          <AxoStatCard label="Job time (p95)" icon="mdi-timer-outline" color="brand-3" :loading="loading"
                       :value="formatMs(p95)" :caption="`Median ${formatMs(median)}`" :good-when-up="false"
                       :change="percentChange(p95, p95Before)" :change-label="`vs the previous ${rangeTitle}`"
                       :trend="p95Trend"
                       help="95 % of jobs took at most this long, from arriving at the platform to their result (waits included)." />
        </v-col>
        <v-col cols="12" sm="6" lg="3">
          <AxoStatCard v-if="money.count" label="Bills so far" icon="mdi-receipt-text-outline" color="warning"
                       :loading="loading" :value="formatMoney(money.cents)"
                       :caption="`This rental period · ${money.count} VE${money.count === 1 ? '' : 's'}`"
                       help="The open bill of each VE you own or administer: the plan fee plus usage so far. Money is only shown to VE Owners and Admins." />
          <AxoStatCard v-else label="Requests" icon="mdi-swap-horizontal" color="info" :loading="loading"
                       :value="formatNumber(requests)" :change="percentChange(requests, requestsBeforeTotal)"
                       :change-label="`vs the previous ${rangeTitle}`" :trend="requestTrend"
                       help="Job submissions the endpoints received." />
        </v-col>
      </v-row>

      <!-- Jobs over time + outcomes -->
      <v-row density="compact" class="mb-2">
        <v-col cols="12" lg="8">
          <AxoPanel title="Jobs over time" icon="mdi-chart-bar"
                    help="Finished jobs per step, by how they ended. Drag the bar below to zoom.">
            <AxoTimeChart :series="jobSeries" :loading="loading" :area="false" zoom :height="300" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="4">
          <AxoPanel title="How jobs ended" icon="mdi-chart-donut" help="Every job in this period, by outcome.">
            <AxoChart :option="outcomeOption" :loading="loading" :empty="!jobs" :height="300" />
          </AxoPanel>
        </v-col>
      </v-row>

      <!-- Times + resources -->
      <v-row density="compact" class="mb-2">
        <v-col cols="12" md="6">
          <AxoPanel title="Job time" icon="mdi-timer-sand"
                    help="Median and 95th percentile of the time from arrival to result.">
            <AxoTimeChart :series="timeSeries" :loading="loading" :format="formatMs" :height="260" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" md="6">
          <AxoPanel title="Endpoint load" icon="mdi-memory"
                    help="Average CPU and memory use of the endpoints running your jobs.">
            <AxoTimeChart :series="resourceSeries" :loading="loading" :format="v => formatPercent(v)" :height="260" />
          </AxoPanel>
        </v-col>
      </v-row>

      <!-- Top functions + when -->
      <v-row density="compact" class="mb-2">
        <v-col cols="12" lg="7">
          <AxoPanel title="Busiest functions" icon="mdi-function-variant" flush
                    help="The functions with the most jobs in this period.">
            <v-table class="axo-dash__top">
              <thead>
                <tr>
                  <th>Function</th><th class="text-end">Jobs</th><th class="text-end">Failed</th>
                  <th class="text-end">Avg time</th><th v-if="top?.money" class="text-end">Cost</th>
                  <th style="width: 30%" />
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in top?.rows || []" :key="row.function_id">
                  <td class="font-weight-medium text-truncate" style="max-width: 220px">{{ row.function_id }}</td>
                  <td class="text-end">{{ formatNumber(row.attempts) }}</td>
                  <td class="text-end" :class="{ 'text-error': row.failed }">{{ formatPercent(100 * row.failed / row.attempts, 1) }}</td>
                  <td class="text-end">{{ formatMs(row.avg_execution_ms) }}</td>
                  <td v-if="top?.money" class="text-end">{{ formatUcents(row.cost_ucents) }}</td>
                  <td>
                    <v-progress-linear :model-value="100 * row.attempts / topMax" color="primary" rounded height="6" />
                  </td>
                </tr>
                <tr v-if="!loading && !top?.rows?.length">
                  <td colspan="6" class="text-center axo-muted py-6">No jobs in this period yet</td>
                </tr>
              </tbody>
            </v-table>
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="5">
          <AxoPanel title="When jobs run" icon="mdi-calendar-clock"
                    :subtitle="`Weekday × hour, ${timeZoneLabel()}, ${heatmapTitle}`">
            <AxoHeatmap :cells="heatmap?.attempts || []" :loading="loading" :height="300" />
          </AxoPanel>
        </v-col>
      </v-row>

      <!-- My VEs -->
      <div class="d-flex align-center mt-6 mb-3">
        <h2 class="text-h6 font-weight-bold">My VEs</h2>
        <AxoHelp title="My VEs" text="Every VE you have a role in. Click one to show only its figures above." />
      </div>
      <v-row density="compact">
        <v-col v-for="ve in ves" :key="ve.ve_id" cols="12" sm="6" lg="3">
          <v-card class="axo-dash__ve pa-4 h-100" :class="{ 'axo-dash__ve--on': selected.includes(ve.ve_id) }"
                  @click="toggle(ve.ve_id)">
            <div class="d-flex align-center ga-2">
              <v-icon color="primary">mdi-cube-outline</v-icon>
              <span class="text-subtitle-1 font-weight-bold text-truncate">{{ ve.name }}</span>
              <v-spacer />
              <AxoStatusChip kind="lifecycle" :value="ve.lifecycle" :reason="ve.lifecycle_reason" size="x-small" />
            </div>
            <div class="text-caption axo-muted mt-1">
              {{ planName(ve.plan_id) }}<template v-if="ve.owner.kind !== 'team'"> · {{ ve.owner.name || ve.owner.id }}</template>
            </div>
            <div class="d-flex flex-wrap ga-2 mt-3">
              <v-chip v-if="ve.owner.kind === 'team'" size="x-small" variant="tonal" color="primary" prepend-icon="mdi-account-group-outline"
                      :to="`/teams/${ve.owner.name}`" @click.stop>{{ ve.owner.name }}</v-chip>
              <v-chip size="x-small" variant="tonal" prepend-icon="mdi-account-key-outline">{{ roleName(ve.my_role) }}</v-chip>
              <v-chip size="x-small" variant="tonal" prepend-icon="mdi-server-network">
                {{ ve.endpoints_running }}/{{ ve.target_endpoints }} endpoints
              </v-chip>
              <AxoStatusChip v-if="ve.health" kind="health" :value="ve.health" size="x-small" />
            </div>
            <v-divider class="my-3" />
            <div class="d-flex align-end">
              <div>
                <div class="text-caption axo-muted">This period</div>
                <div class="text-body-1 font-weight-bold">
                  {{ ve.current_usage ? `${formatNumber(ve.current_usage.attempts)} job${ve.current_usage.attempts === 1 ? '' : 's'}` : '—' }}
                </div>
              </div>
              <v-spacer />
              <div v-if="ve.current_total_cents != null" class="text-end">
                <div class="text-caption axo-muted">Bill so far</div>
                <div class="text-body-1 font-weight-bold">{{ formatMoney(ve.current_total_cents) }}</div>
              </div>
            </div>
            <div v-if="ve.rental?.period_end" class="text-caption axo-muted mt-1">
              {{ ve.rental.cancel_at_period_end ? 'Ends' : 'Renews' }} {{ formatDate(ve.rental.period_end) }}
            </div>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { ChartAnswer, HeatmapAnswer, SummaryAnswer, TopAnswer, VeCard } from 'axo-ui-kit/app/types/cp'
import type { RangeValue } from 'axo-ui-kit/app/composables/useTimeRange'

// The person's dashboard: figures over every VE they have a role in (or
// the ones picked), merged by the Control Plane (/me/stats/*), plus their
// VE cards (/me/overview). Range and VE choice live in the URL.
useHead({ title: 'Dashboard · axo' })
const me = useMeStore()
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const live = useLive()

// ── the URL keeps the range and the VEs ────────────────────────────────────
const isRange = (v: unknown): v is RangeValue => RANGE_PRESETS.some(r => r.value === v)
const { range, window, previous, touch } = useTimeRange(isRange(route.query.range) ? route.query.range : undefined,
  'axo-dashboard-range')
const selected = ref<string[]>(String(route.query.ve || '').split(',').filter(Boolean))
watch([range, selected], () => {
  router.replace({ query: { ...route.query, range: range.value, ve: selected.value.join(',') || undefined } })
})
const rangeTitle = computed(() => RANGE_PRESETS.find(r => r.value === range.value)!.title)
// One chart step per range, so bars stay readable: 60 / 96 / 84 / 120 points.
const STEP_S: Record<RangeValue, number> = { '1h': 60, '24h': 900, '7d': 7200, '30d': 21600 }

// ── data ───────────────────────────────────────────────────────────────────
const ves = ref<VeCard[]>([])
const summary = ref<SummaryAnswer | null>(null)
const summaryBefore = ref<SummaryAnswer | null>(null)
const jobsChart = ref<ChartAnswer | null>(null)
const resources = ref<{ cpu: ChartAnswer; ram: ChartAnswer } | null>(null)
const requestsNow = ref<SummaryAnswer | null>(null)
const requestsBefore = ref<SummaryAnswer | null>(null)
const top = ref<TopAnswer | null>(null)
const heatmap = ref<HeatmapAnswer | null>(null)
const loading = ref(true)
const loaded = ref(false)
const error = ref<unknown>(null)

const veItems = computed(() => ves.value.map(v => ({ title: v.name, value: v.ve_id })))
// Shortcut in the VE picker: every VE one of my teams owns.
const teamsWithVes = computed(() => {
  const mine = new Set((me.me?.teams ?? []).map(t => t.team_id))
  const by = new Map<string, string[]>()
  for (const v of ves.value) {
    if (v.owner.kind === 'team' && mine.has(v.owner.id)) by.set(v.owner.name ?? v.owner.id, [...(by.get(v.owner.name ?? v.owner.id) ?? []), v.ve_id])
  }
  return [...by].map(([name, ve_ids]) => ({ name, ve_ids }))
})
const scopeLabel = computed(() => {
  if (!selected.value.length) return ves.value.length === 1 ? 'your VE' : `all ${ves.value.length} VEs`
  return selected.value.map(id => ves.value.find(v => v.ve_id === id)?.name ?? id).join(', ')
})
const heatmapTitle = computed(() => (range.value === '1h' || range.value === '24h' ? 'last 7 days' : `last ${rangeTitle.value}`))

function q(extra: Record<string, string | number | undefined>, win = window.value) {
  const params = new URLSearchParams()
  if (selected.value.length) params.set('ve_ids', selected.value.join(','))
  params.set('start', String(win.start))
  params.set('end', String(win.end))
  for (const [k, v] of Object.entries(extra)) if (v !== undefined) params.set(k, String(v))
  return params.toString()
}

let generation = 0
async function load() {
  const mine = ++generation
  touch()
  error.value = null
  loading.value = !loaded.value || loading.value
  try {
    const overview = await apiFetch<VeCard[]>('/me/overview')
    if (mine !== generation) return
    ves.value = overview
    const known = selected.value.filter(id => overview.some(v => v.ve_id === id))
    if (known.length !== selected.value.length) selected.value = known      // (a new array would reload)
    if (!overview.length) return
    const heatStart = window.value.end - Math.max(7 * 86400, window.value.end - window.value.start)
    // The period before may be past the plans' retention: then no comparison.
    const [s, sb, jc, cpu, ram, rn, rb, t, h] = await Promise.all([
      apiFetch<SummaryAnswer>(`/me/stats/summary?${q({ metric: 'job.total_ms', group_by: 'outcome', statistics: 'count,median,p95' })}`),
      apiFetch<SummaryAnswer>(`/me/stats/summary?${q({ metric: 'job.total_ms', group_by: 'outcome', statistics: 'count,median,p95' }, previous.value)}`).catch(() => null),
      apiFetch<ChartAnswer>(`/me/stats/chart?${q({ metric: 'job.total_ms', group_by: 'outcome', statistics: 'count,median,p95', step_s: STEP_S[range.value] })}`),
      apiFetch<ChartAnswer>(`/me/stats/chart?${q({ metric: 'resource.cpu_pct', statistics: 'avg', step_s: STEP_S[range.value] })}`),
      apiFetch<ChartAnswer>(`/me/stats/chart?${q({ metric: 'resource.ram_pct', statistics: 'avg', step_s: STEP_S[range.value] })}`),
      apiFetch<SummaryAnswer>(`/me/stats/summary?${q({ metric: 'requests.received', statistics: 'total' })}`),
      apiFetch<SummaryAnswer>(`/me/stats/summary?${q({ metric: 'requests.received', statistics: 'total' }, previous.value)}`).catch(() => null),
      apiFetch<TopAnswer>(`/me/stats/top?${q({ by: 'function', limit: 8 })}`),
      apiFetch<HeatmapAnswer>(`/me/stats/heatmap?${q({ time_zone: currentSettings().time_zone },
        { start: heatStart, end: window.value.end })}`),
    ])
    if (mine !== generation) return
    summary.value = s
    summaryBefore.value = sb
    jobsChart.value = jc
    resources.value = { cpu, ram }
    requestsNow.value = rn
    requestsBefore.value = rb
    top.value = t
    heatmap.value = h
  } catch (e) {
    if (mine === generation) error.value = e
  } finally {
    if (mine === generation) {
      loading.value = false
      loaded.value = true
    }
  }
}

watch([range, selected], () => { loading.value = true; load() })
// Short windows move: refresh every 30 s (only while the page is visible).
usePolling(() => (loaded.value && (range.value === '1h' || range.value === '24h') ? load() : undefined), 30_000)
onMounted(() => { if (!loaded.value) load() })
live.onVe((card, veId) => {
  const i = ves.value.findIndex(v => v.ve_id === veId)
  if (!card) { if (i >= 0) ves.value.splice(i, 1); return }
  // The socket's card has no bill figures: keep the ones we have.
  if (i >= 0) ves.value[i] = { ...ves.value[i], ...card, current_total_cents: ves.value[i]!.current_total_cents,
    current_usage: ves.value[i]!.current_usage }
  else load()
})

// ── figures ────────────────────────────────────────────────────────────────
function totals(answer: SummaryAnswer | null) {
  const groups = answer?.groups ?? []
  const count = groups.reduce((a, g) => a + (g.values.count ?? 0), 0)
  const ok = groups.find(g => g.group === 'completed')?.values.count ?? 0
  return { count, rate: count ? (100 * ok) / count : null }
}
const jobs = computed(() => totals(summary.value).count)
const jobsBefore = computed(() => (summaryBefore.value ? totals(summaryBefore.value).count : null))
const successRate = computed(() => totals(summary.value).rate)
const successBefore = computed(() => totals(summaryBefore.value).rate)
// Percentiles can't be added across groups; the completed jobs' are the useful ones.
const completedValues = (a: SummaryAnswer | null) => a?.groups.find(g => g.group === 'completed')?.values
const p95 = computed(() => completedValues(summary.value)?.p95 ?? null)
const median = computed(() => completedValues(summary.value)?.median ?? null)
const p95Before = computed(() => completedValues(summaryBefore.value)?.p95 ?? null)
const requests = computed(() => requestsNow.value?.groups[0]?.values.total ?? 0)
const requestsBeforeTotal = computed(() => (requestsBefore.value ? requestsBefore.value.groups[0]?.values.total ?? 0 : null))
const shown = computed(() => (selected.value.length ? ves.value.filter(v => selected.value.includes(v.ve_id)) : ves.value))
const money = computed(() => {
  const billed = shown.value.filter(v => v.current_total_cents != null)
  return { count: billed.length, cents: billed.reduce((a, v) => a + (v.current_total_cents ?? 0), 0) }
})

const jobTrend = computed(() => chartTotals(jobsChart.value, 'count'))
const p95Trend = computed(() => chartSeries(jobsChart.value, 'p95').find(s => s.name === 'completed')?.points.map(p => p[1]) ?? [])
const requestTrend = computed(() => jobTrend.value)

const jobSeries = computed(() => chartSeries(jobsChart.value, 'count', {
  name: outcomeName, color: outcomeColor, type: 'bar', stack: 'jobs', fill: 0, order: OUTCOME_ORDER,
}))
const timeSeries = computed(() => {
  const completed = (stat: string) => chartSeries(jobsChart.value, stat).find(s => s.name === 'completed')
  const med = completed('median')
  const p = completed('p95')
  return [
    ...(med ? [{ ...med, name: 'Median', color: CHART_COLORS.blue }] : []),
    ...(p ? [{ ...p, name: 'p95', color: CHART_COLORS.violet, dashed: true }] : []),
  ]
})
const resourceSeries = computed(() => [
  ...chartSeries(resources.value?.cpu, 'avg', { name: () => 'CPU', color: () => CHART_COLORS.cyan }),
  ...chartSeries(resources.value?.ram, 'avg', { name: () => 'Memory', color: () => CHART_COLORS.violet }),
])
const topMax = computed(() => Math.max(1, ...(top.value?.rows ?? []).map(r => r.attempts)))

const c = useChartTheme()
const outcomeOption = computed(() => {
  const groups = [...(summary.value?.groups ?? [])].sort((a, b) =>
    OUTCOME_ORDER.indexOf(a.group ?? '') - OUTCOME_ORDER.indexOf(b.group ?? ''))
  return {
    tooltip: { trigger: 'item', valueFormatter: (v: number) => formatNumber(v) },
    legend: { top: 'bottom' },
    series: [{
      type: 'pie', radius: ['52%', '76%'], center: ['50%', '44%'], avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: c.value.dark ? '#1B1C1F' : '#FFFFFF', borderWidth: 2 },
      label: { show: true, position: 'center', formatter: () => `${successRate.value == null ? '—' : successRate.value.toFixed(1)} %\ncompleted`,
        fontSize: 16, fontWeight: 700, color: c.value.text, lineHeight: 22 },
      emphasis: { scale: true, scaleSize: 4 },
      data: groups.map(g => ({ name: outcomeName(g.group), value: g.values.count ?? 0,
        itemStyle: { color: outcomeColor(g.group) } })),
    }],
  }
})

// ── VE cards ───────────────────────────────────────────────────────────────
function toggle(id: string) {
  selected.value = selected.value.includes(id) ? selected.value.filter(v => v !== id) : [...selected.value, id]
}
const planName = (id: string) => id ? id[0]!.toUpperCase() + id.slice(1) : '—'
const roleName = (role?: string | null) => (role ? role[0]!.toUpperCase() + role.slice(1) : '—')
</script>

<style scoped>
.axo-dash__ves { min-width: 220px; max-width: 420px; }
.axo-dash__ve { cursor: pointer; transition: box-shadow 0.2s ease, transform 0.2s ease, border-color 0.2s ease; border: 1px solid transparent; }
.axo-dash__ve:hover { box-shadow: 0 8px 24px rgba(0, 134, 255, 0.14); transform: translateY(-1px); }
.axo-dash__ve--on { border-color: rgb(var(--v-theme-primary)); }
.axo-dash__top :deep(td), .axo-dash__top :deep(th) { white-space: nowrap; }
</style>
