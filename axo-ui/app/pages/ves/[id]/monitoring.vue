<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <span class="text-body-2 axo-muted">History kept here: the plan's retention; older figures are hidden.</span>
      <v-spacer />
      <AxoRangePicker v-model="range" />
    </div>

    <AxoErrorAlert v-if="error" :error="error" title="The figures couldn't load" :retry="load" class="mb-4" />

    <v-row density="compact" class="mb-2">
      <v-col cols="12" lg="8">
        <AxoPanel title="Jobs over time" icon="mdi-chart-bar" help="Finished jobs per step, by how they ended. Drag the bar below to zoom.">
          <AxoTimeChart :series="jobSeries" :loading="loading" :area="false" zoom :height="300" />
        </AxoPanel>
      </v-col>
      <v-col cols="12" lg="4">
        <AxoPanel title="Job time" icon="mdi-timer-outline" help="How long completed jobs took, from arriving at the platform to their result (waits included).">
          <AxoTimeChart :series="timeSeries" :loading="loading" :format="formatMs" :height="300" />
        </AxoPanel>
      </v-col>
    </v-row>

    <v-row density="compact" class="mb-2">
      <v-col cols="12" md="6">
        <AxoPanel title="Where the time goes" icon="mdi-layers-outline"
                  help="Median time per part of a job: waiting at each layer of the platform, starting a runner (cold start), and running your code.">
          <AxoTimeChart :series="partSeries" :loading="loading" :format="formatMs" :height="260" />
        </AxoPanel>
      </v-col>
      <v-col cols="12" md="6">
        <AxoPanel title="CPU and memory" icon="mdi-memory" help="Average use of the endpoints' CPU and memory, in % of their size.">
          <AxoTimeChart :series="resourceSeries" :loading="loading" :format="(v: number) => `${v.toFixed(0)} %`" :height="260" />
        </AxoPanel>
      </v-col>
    </v-row>

    <v-row density="compact" class="mb-2">
      <v-col cols="12" md="6">
        <AxoPanel title="Requests" icon="mdi-swap-horizontal" help="Submissions the endpoints received, and the ones they turned away (queue full) or that waited too long.">
          <AxoTimeChart :series="requestSeries" :loading="loading" :area="false" :height="260" />
        </AxoPanel>
      </v-col>
      <v-col cols="12" md="6">
        <AxoPanel title="Queue length" icon="mdi-tray-full" help="Average number of jobs waiting in the endpoints' queues.">
          <AxoTimeChart :series="queueSeries" :loading="loading" :height="260" />
        </AxoPanel>
      </v-col>
    </v-row>

    <v-row density="compact">
      <v-col cols="12" lg="7">
        <AxoPanel title="Busiest functions" icon="mdi-function-variant" flush>
          <v-table class="bg-transparent">
            <thead><tr>
              <th>Function</th><th class="text-end">Jobs</th><th class="text-end">Failed</th><th class="text-end">Avg run</th>
              <th v-if="top?.money" class="text-end">Cost</th>
            </tr></thead>
            <tbody>
              <tr v-for="row in top?.rows || []" :key="`${row.function_id}`">
                <td class="font-weight-medium text-truncate" style="max-width: 240px">{{ functionName(row.function_id) }}</td>
                <td class="text-end">{{ formatNumber(row.attempts) }}</td>
                <td class="text-end" :class="{ 'text-error': row.failed }">{{ formatPercent(100 * row.failed / row.attempts, 1) }}</td>
                <td class="text-end">{{ formatMs(row.avg_execution_ms) }}</td>
                <td v-if="top?.money" class="text-end">{{ formatUcents(row.cost_ucents) }}</td>
              </tr>
              <tr v-if="!loading && !top?.rows?.length"><td colspan="5" class="text-center axo-muted py-6">No jobs in this period.</td></tr>
            </tbody>
          </v-table>
        </AxoPanel>
      </v-col>
      <v-col cols="12" lg="5">
        <AxoPanel :title="`When jobs run (${heatmapTitle})`" icon="mdi-calendar-clock" help="Jobs by weekday and hour, in your time zone (Settings).">
          <AxoHeatmap :cells="heatmap?.attempts || []" :loading="loading" :height="300" />
        </AxoPanel>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import type { ChartAnswer, HeatmapAnswer, TopAnswer } from 'axo-ui-kit/app/types/cp'
import type { RangeValue } from 'axo-ui-kit/app/composables/useTimeRange'

// One VE's figures (/ves/{id}/stats/*): jobs by outcome, job time, where
// the time goes, CPU / memory, requests, queues, busiest functions and the
// weekday × hour heatmap. The range lives in the URL.
const { id, api, cp } = useCurrentVe()
const route = useRoute()
const router = useRouter()

const isRange = (v: unknown): v is RangeValue => RANGE_PRESETS.some(r => r.value === v)
const { range, window, touch } = useTimeRange(isRange(route.query.range) ? route.query.range : undefined, 'axo-ve-range')
watch(range, () => router.replace({ query: { ...route.query, range: range.value } }))
const STEP_S: Record<RangeValue, number> = { '1h': 60, '24h': 900, '7d': 7200, '30d': 21600 }
const heatmapTitle = computed(() => (range.value === '1h' || range.value === '24h' ? 'last 7 days'
  : `last ${RANGE_PRESETS.find(r => r.value === range.value)!.title}`))

const charts = ref<Record<string, ChartAnswer | null>>({})
const top = ref<TopAnswer | null>(null)
const heatmap = ref<HeatmapAnswer | null>(null)
const names = ref<Record<string, string>>({})
const loading = ref(true)
const error = ref<unknown>(null)

const CHARTS: Record<string, Record<string, string>> = {
  jobs: { metric: 'job.total_ms', group_by: 'outcome', statistics: 'count,median,p95' },
  wait: { metric: 'job.wait_ms', group_by: 'layer', statistics: 'median' },
  cold: { metric: 'job.cold_start_ms', statistics: 'median' },
  exec: { metric: 'job.exec_ms', statistics: 'median' },
  cpu: { metric: 'resource.cpu_pct', statistics: 'avg' },
  ram: { metric: 'resource.ram_pct', statistics: 'avg' },
  received: { metric: 'requests.received', statistics: 'total' },
  rejected: { metric: 'requests.rejected', statistics: 'total' },
  timedOut: { metric: 'requests.timed_out', statistics: 'total' },
  queue: { metric: 'queue.length', statistics: 'avg' },
}

function q(extra: Record<string, string | number>, win = window.value) {
  return new URLSearchParams({ start: String(win.start), end: String(win.end),
    ...Object.fromEntries(Object.entries(extra).map(([k, v]) => [k, String(v)])) }).toString()
}

let generation = 0
async function load() {
  const mine = ++generation
  touch()
  error.value = null
  try {
    const step = STEP_S[range.value]
    const keys = Object.keys(CHARTS)
    const heatStart = window.value.end - Math.max(7 * 86400, window.value.end - window.value.start)
    const [answers, t, h] = await Promise.all([
      Promise.all(keys.map(k => cp<ChartAnswer>(`/stats/chart?${q({ ...CHARTS[k]!, step_s: step })}`).catch(() => null))),
      cp<TopAnswer>(`/stats/top?${q({ by: 'function', limit: 10 })}`),
      cp<HeatmapAnswer>(`/stats/heatmap?${q({ time_zone: currentSettings().time_zone },
        { start: heatStart, end: window.value.end })}`),
    ])
    if (mine !== generation) return
    charts.value = Object.fromEntries(keys.map((k, i) => [k, answers[i] ?? null]))
    top.value = t
    heatmap.value = h
  } catch (err) {
    if (mine === generation) error.value = err
  } finally {
    if (mine === generation) loading.value = false
  }
}

async function loadNames() {
  try {
    const functions = await api<Array<{ function_id: string; name: string }>>('/functions')
    names.value = Object.fromEntries(functions.map(f => [f.function_id, f.name]))
  } catch { /* the ids are shown instead */ }
}
const functionName = (fid?: string) => (fid ? names.value[fid] ?? `${fid.slice(0, 12)}…` : '—')

watch(range, () => { loading.value = true; load() })
watch(id, () => { loading.value = true; load(); loadNames() })
usePolling(() => (range.value === '1h' || range.value === '24h' ? load() : undefined), 30_000)
onMounted(loadNames)

// ── series ─────────────────────────────────────────────────────────────────
const jobSeries = computed(() => chartSeries(charts.value.jobs, 'count', {
  name: outcomeName, color: outcomeColor, type: 'bar', stack: 'jobs', fill: 0, order: OUTCOME_ORDER,
}))
const timeSeries = computed(() => {
  const completed = (stat: string) => chartSeries(charts.value.jobs, stat).find(s => s.name === 'completed')
  const med = completed('median')
  const p = completed('p95')
  return [
    ...(med ? [{ ...med, name: 'Median', color: CHART_COLORS.blue }] : []),
    ...(p ? [{ ...p, name: 'p95', color: CHART_COLORS.violet, dashed: true }] : []),
  ]
})
const one = (key: string, stat: string, name: string, color: string, extra: Record<string, unknown> = {}) =>
  chartSeries(charts.value[key], stat, { name: () => name, color: () => color, ...extra })
const LAYERS: Record<string, { name: string; color: string }> = {
  control_plane: { name: 'Waiting: platform front door', color: CHART_COLORS.grey },
  vem: { name: 'Waiting: picking an endpoint', color: CHART_COLORS.pink },
  endpoint: { name: 'Waiting: endpoint queue', color: CHART_COLORS.orange },
  runner: { name: 'Waiting: free runner', color: CHART_COLORS.teal },
}
const partSeries = computed(() => [
  ...chartSeries(charts.value.wait, 'median', { name: g => LAYERS[g ?? '']?.name ?? `Waiting (${g})`,
    color: g => LAYERS[g ?? '']?.color, order: Object.keys(LAYERS) }),
  ...one('cold', 'median', 'Cold start', CHART_COLORS.violet),
  ...one('exec', 'median', 'Running', CHART_COLORS.blue),
])
const resourceSeries = computed(() => [
  ...one('cpu', 'avg', 'CPU', CHART_COLORS.cyan),
  ...one('ram', 'avg', 'Memory', CHART_COLORS.violet),
])
const requestSeries = computed(() => [
  ...one('received', 'total', 'Received', CHART_COLORS.blue, { type: 'bar', fill: 0 }),
  ...one('rejected', 'total', 'Turned away', CHART_COLORS.red, { type: 'bar', fill: 0 }),
  ...one('timedOut', 'total', 'Waited too long', CHART_COLORS.orange, { type: 'bar', fill: 0 }),
])
const queueSeries = computed(() => one('queue', 'avg', 'Waiting jobs', CHART_COLORS.teal))
</script>
