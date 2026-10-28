<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-4">
      <v-btn :to="`/ves/${id}/endpoints`" variant="text" size="small" prepend-icon="mdi-arrow-left" class="text-none px-2">Endpoints</v-btn>
    </div>

    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: the endpoint shows here once it runs.
    </v-alert>
    <AxoErrorAlert v-if="error" :error="error" :title="notFound ? 'This endpoint is not in the VE' : 'The endpoint couldn\'t load'"
                   :retry="notFound ? undefined : load" class="mb-4" />
    <AxoSkeleton v-if="vemUp && !ep && !error" :lines="8" />

    <template v-if="ep">
      <div class="d-flex align-center flex-wrap ga-2 mb-4">
        <v-icon color="primary">mdi-server-outline</v-icon>
        <span class="text-h6 axo-mono text-truncate" style="max-width: 100%">{{ ep.endpoint_id }}</span>
        <AxoStatusChip kind="endpoint" :value="state" :reason="ep.lifecycle_reason" />
        <v-chip v-if="isLeader(ep)" size="small" color="primary" variant="tonal" prepend-icon="mdi-crown-outline">Leader</v-chip>
        <v-spacer />
        <template v-if="can('admin')">
          <AxoBtn look="plain" prepend-icon="mdi-restart" :disabled="state !== 'Running'"
                  :loading="actions.busy.value === `restart:${ep.endpoint_id}`" @click="act(actions.restart(ep!))">Restart</AxoBtn>
          <AxoBtn v-if="isPresent(ep)" look="danger" prepend-icon="mdi-minus-circle-outline" :disabled="atMinimum"
                  :title="atMinimum ? 'The VE is at its minimum number of endpoints' : undefined"
                  :loading="actions.busy.value === `remove:${ep.endpoint_id}`"
                  @click="act(actions.remove(ep!, view?.settings.target_endpoints ?? 1))">Remove</AxoBtn>
          <AxoBtn v-if="ep.purge_eligible" look="danger" prepend-icon="mdi-delete-outline"
                  :loading="actions.busy.value === `purge:${ep.endpoint_id}`" @click="forget">Forget</AxoBtn>
        </template>
      </div>

      <v-row density="compact" class="mb-2">
        <v-col cols="12" lg="7">
          <AxoPanel title="About it" icon="mdi-information-outline">
            <AxoKeyValue :items="facts" :columns="3" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="5">
          <AxoPanel title="Use now" icon="mdi-memory" help="Its container's CPU and memory, live, in % of the endpoint's size (runner containers count separately).">
            <template v-if="samples.length && samples.at(-1)!.available">
              <div class="d-flex align-center ga-3 mb-1">
                <span class="axo-live-label">CPU</span>
                <AxoSparkline :values="samples.map(s => cpuOfSize(s, ep!.cpu_cores))" :color="CHART_COLORS.cyan" :height="44" class="flex-grow-1" />
                <span class="axo-live-value">{{ formatPercent(cpuOfSize(samples.at(-1), ep.cpu_cores)) }}</span>
              </div>
              <div class="d-flex align-center ga-3 mb-2">
                <span class="axo-live-label">Memory</span>
                <AxoSparkline :values="samples.map(s => memoryOfSize(s))" :color="CHART_COLORS.violet" :height="44" class="flex-grow-1" />
                <span class="axo-live-value">{{ formatPercent(memoryOfSize(samples.at(-1))) }}</span>
              </div>
              <div class="text-caption axo-muted">
                {{ formatBytes(samples.at(-1)!.memory_usage) }} of {{ formatBytes(samples.at(-1)!.memory_limit) }} ·
                network in {{ formatBytes(samples.at(-1)!.network_rx) }}, out {{ formatBytes(samples.at(-1)!.network_tx) }}
              </div>
            </template>
            <div v-else class="text-body-2 axo-muted py-4">
              {{ state !== 'Running' ? 'Only a running endpoint has live figures.'
                : live.connected.value ? (samples.length ? 'Its container gives no figures right now.' : 'Waiting for the first figures…')
                  : 'The live feed is not connected.' }}
            </div>
          </AxoPanel>
        </v-col>
      </v-row>

      <AxoPanel v-if="pools.length" title="Runners" icon="mdi-package-variant-closed" class="mb-4" flush
                help="Functions with their own container (a Python version or packages): how many of their runners live on this endpoint, and how many wait for work.">
        <v-table class="bg-transparent">
          <thead><tr><th>Function</th><th>Version</th><th class="text-end">Runners</th><th class="text-end">Idle</th></tr></thead>
          <tbody>
            <tr v-for="p in pools" :key="p.key">
              <td class="text-truncate" style="max-width: 280px">{{ fname(p.function_id) }}</td>
              <td>v{{ p.version }}</td>
              <td class="text-end">{{ p.live }}</td>
              <td class="text-end">{{ p.idle }}</td>
            </tr>
          </tbody>
        </v-table>
      </AxoPanel>

      <div class="d-flex align-center flex-wrap ga-3 mb-3 mt-2">
        <span class="text-subtitle-1 font-weight-medium">Over time</span>
        <v-spacer />
        <AxoRangePicker v-model="range" />
      </div>
      <v-row density="compact" class="mb-2">
        <v-col cols="12" md="6">
          <AxoPanel title="Jobs" icon="mdi-chart-bar" help="Jobs this endpoint finished per step, by how they ended.">
            <AxoTimeChart :series="jobSeries" :loading="chartsLoading" :area="false" :height="240" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" md="6">
          <AxoPanel title="Job time" icon="mdi-timer-outline" help="How long its completed jobs took, waits included.">
            <AxoTimeChart :series="timeSeries" :loading="chartsLoading" :format="formatMs" :height="240" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" md="6">
          <AxoPanel title="Requests" icon="mdi-swap-horizontal" help="Submissions it received.">
            <AxoTimeChart :series="requestSeries" :loading="chartsLoading" :area="false" :height="220" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" md="6">
          <AxoPanel title="Queue length" icon="mdi-tray-full" help="Average number of jobs waiting in its queues.">
            <AxoTimeChart :series="queueSeries" :loading="chartsLoading" :height="220" />
          </AxoPanel>
        </v-col>
      </v-row>

      <AxoPanel title="History" icon="mdi-history" stacked>
        <AxoEmptyState v-if="!history.length" icon="mdi-history" title="Nothing recorded yet" />
        <v-timeline v-else side="end" density="compact" align="start" truncate-line="both" class="axo-activity">
          <v-timeline-item v-for="row in history" :key="row.event_id" size="x-small" fill-dot
                           :dot-color="ACTIVITY_AREAS[areaOf(row)]!.color">
            <div class="d-flex flex-wrap align-baseline ga-2">
              <span class="text-body-2">{{ describeActivity(row, fname) }}</span>
              <v-spacer />
              <span class="text-caption axo-muted" :title="formatExactTime(Date.parse(row.created_at) / 1000)">{{ timeAgo(Date.parse(row.created_at) / 1000) }}</span>
            </div>
          </v-timeline-item>
        </v-timeline>
        <div v-if="historyMore" class="d-flex justify-center">
          <v-btn variant="tonal" size="small" :loading="historyLoadingMore" prepend-icon="mdi-chevron-down"
                 @click="historyFeed.loadMore">Load more</v-btn>
        </div>
      </AxoPanel>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { ChartAnswer } from 'axo-ui-kit/app/types/cp'
import type { RangeValue } from 'axo-ui-kit/app/composables/useTimeRange'
import type { KeyValueItem } from 'axo-ui-kit/app/components/AxoKeyValue.vue'

// One endpoint: what it is (size, role, binds, counters), its container's
// CPU / memory live (the VEM's feed for this endpoint), its runners, its
// figures over time (/stats/chart filtered to this endpoint) and its history.
const { id, ve, can, vemUp, api, cp } = useCurrentVe()
const route = useRoute()
const router = useRouter()
const actions = useEndpointActions()
const endpointId = computed(() => String(route.params.endpointId ?? ''))
useHead(() => ({ title: `${endpointId.value} · ${ve.value?.name ?? 'VE'} · axo` }))

const ep = ref<EndpointRow | null>(null)
const view = ref<{ settings: Record<string, any> } | null>(null)
const present = ref(0)
const historyFeed = useActivityFeed(() => (vemUp.value ? `/endpoints/${endpointId.value}/history` : null), () => ({}), 30)
const { rows: history, more: historyMore, loadingMore: historyLoadingMore } = historyFeed
watch(endpointId, historyFeed.load)
const names = ref<Record<string, string>>({})
const error = ref<unknown>(null)
const notFound = computed(() => (error.value as any)?.status === 404)

async function load() {
  if (!vemUp.value) return
  error.value = null
  try {
    const [one, list, v] = await Promise.all([
      api<EndpointRow>(`/endpoints/${endpointId.value}`), api<EndpointRow[]>('/endpoints'), api('/ve'),
      historyFeed.refresh(),
    ])
    ep.value = one
    present.value = list.filter(isPresent).length
    view.value = v
  } catch (err) { error.value = err }
}
async function loadNames() {
  if (!vemUp.value) return
  try {
    const functions = await api<Array<{ function_id: string; name: string }>>('/functions')
    names.value = Object.fromEntries(functions.map(f => [f.function_id, f.name]))
  } catch { /* ids instead */ }
}
const fname = (fid?: string) => (fid ? names.value[fid] ?? `${fid.slice(0, 12)}…` : 'a function')
watch([endpointId, vemUp], () => { ep.value = null; samples.value = []; load(); loadNames() })
usePolling(load, 10_000)
onMounted(loadNames)

const state = computed(() => (ep.value ? endpointState(ep.value) : ''))
const atMinimum = computed(() => present.value <= (view.value?.settings.min_endpoints ?? 0))
async function act(done: Promise<boolean>) { if (await done) load() }
async function forget() {
  if (await actions.purge(ep.value!)) router.push(`/ves/${id.value}/endpoints`)
}

// ── live figures ───────────────────────────────────────────────────────────
const samples = ref<EndpointStats[]>([])
const live = useVeLive(() => (vemUp.value && endpointId.value ? `endpoints/${endpointId.value}` : null), (msg) => {
  if (typeof msg?.available === 'boolean') samples.value = [...samples.value.slice(-59), msg]
  else if (msg?.endpoint_id) load()
})

// ── facts ──────────────────────────────────────────────────────────────────
const facts = computed<KeyValueItem[]>(() => {
  const e = ep.value!
  const m = e.metrics
  const since = e.lifecycle_since ? Date.parse(e.lifecycle_since) / 1000 : null
  return [
    { label: 'Status', value: `${state.value}${since ? ` since ${timeAgo(since)}` : ''}` },
    { label: 'Why', value: e.lifecycle_reason || '—' },
    { label: 'Size', value: e.cpu_cores ? `${e.cpu_cores} CPU · ${formatMemoryMb(e.memory_mb)}` : '—' },
    { label: 'Up for', value: e.uptime_ms && state.value === 'Running' ? formatDuration(e.uptime_ms) : '—' },
    { label: 'Role', value: m ? `${m.consensus_role === 'leader' ? 'Leader' : 'Follower'} (term ${m.consensus_term})` : '—',
      help: 'The leader runs every active-object call in order and decides where container runners go; if it goes, the others pick a new one.' },
    { label: 'Failures in a row', value: e.failures ?? 0, help: 'Times this slot failed one after the other; the VE replaces a failed endpoint.' },
    { label: 'Jobs received', value: m ? formatNumber(m.submitted) : '—' },
    { label: 'Completed / failed', value: m ? `${formatNumber(m.completed)} / ${formatNumber(m.failed)}` : '—' },
    { label: 'Turned away', value: m ? formatNumber(m.rejected) : '—', help: 'Submissions refused because its queue was full.' },
    { label: 'Queue', value: m ? `${m.queue_depth} of ${formatNumber(m.queue_capacity)}` : '—' },
    { label: 'Requests address', value: e.router_bind ?? '—', mono: true },
    { label: 'Heartbeat address', value: e.pub_bind ?? '—', mono: true },
  ]
})

const pools = computed(() => Object.entries(ep.value?.metrics?.container_pools ?? {}).map(([key, p]) => {
  const [function_id, version] = splitPoolKey(key)
  return { key, function_id, version, ...p }
}))

// ── figures over time ──────────────────────────────────────────────────────
const isRange = (v: unknown): v is RangeValue => RANGE_PRESETS.some(r => r.value === v)
const { range, window, touch } = useTimeRange(isRange(route.query.range) ? route.query.range : undefined, 'axo-ve-range')
watch(range, () => router.replace({ query: { ...route.query, range: range.value } }))
const STEP_S: Record<RangeValue, number> = { '1h': 60, '24h': 900, '7d': 7200, '30d': 21600 }
const CHARTS: Record<string, Record<string, string>> = {
  jobs: { metric: 'job.total_ms', group_by: 'outcome', statistics: 'count,median,p95' },
  received: { metric: 'requests.received', statistics: 'total' },
  queue: { metric: 'queue.length', statistics: 'avg' },
}
const charts = ref<Record<string, ChartAnswer | null>>({})
const chartsLoading = ref(true)
let generation = 0
async function loadCharts() {
  const mine = ++generation
  touch()
  const q = (extra: Record<string, string>) => new URLSearchParams({
    start: String(window.value.start), end: String(window.value.end), step_s: String(STEP_S[range.value]),
    dims: `endpoint:${endpointId.value}`, ...extra }).toString()
  const keys = Object.keys(CHARTS)
  const answers = await Promise.all(keys.map(k => cp<ChartAnswer>(`/stats/chart?${q(CHARTS[k]!)}`).catch(() => null)))
  if (mine !== generation) return
  charts.value = Object.fromEntries(keys.map((k, i) => [k, answers[i] ?? null]))
  chartsLoading.value = false
}
watch([range, endpointId], () => { chartsLoading.value = true; loadCharts() })
const recent = () => range.value === '1h' || range.value === '24h'
usePolling(() => (recent() ? loadCharts() : undefined), 60_000)
onMounted(() => { if (!recent()) loadCharts() })

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
const requestSeries = computed(() => chartSeries(charts.value.received, 'total', {
  name: () => 'Received', color: () => CHART_COLORS.blue, type: 'bar', fill: 0 }))
const queueSeries = computed(() => chartSeries(charts.value.queue, 'avg', {
  name: () => 'Waiting jobs', color: () => CHART_COLORS.teal }))
</script>

<style scoped>
.axo-live-label { width: 56px; font-size: 0.8rem; opacity: 0.75; }
.axo-live-value { width: 44px; text-align: right; font-variant-numeric: tabular-nums; }
.axo-activity :deep(.v-timeline-item__body) { width: 100%; padding-bottom: 10px; }
</style>
