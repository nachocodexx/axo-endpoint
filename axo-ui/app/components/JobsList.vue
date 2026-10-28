<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-3">
      <v-text-field v-model="jobSearch" density="compact" variant="outlined" hide-details clearable
                    prepend-inner-icon="mdi-magnify" placeholder="Job id" class="axo-filter axo-filter--id" />
      <v-select v-if="!functionId" v-model="filters.function" :items="functionChoices" density="compact" variant="outlined"
                hide-details clearable placeholder="Every function" class="axo-filter" />
      <v-select v-if="functionId" v-model="filters.version" :items="versionChoices" density="compact" variant="outlined"
                hide-details clearable placeholder="Every version" class="axo-filter axo-filter--small" />
      <v-select v-model="filters.status" :items="STATUS_FILTERS" density="compact" variant="outlined" hide-details
                clearable multiple chips closable-chips placeholder="Any status" class="axo-filter" />
      <v-select v-model="filters.fault" :items="FAULT_FILTERS" density="compact" variant="outlined" hide-details
                clearable placeholder="Any failure" class="axo-filter" />
      <v-select v-model="filters.by" :items="STARTED_BY" density="compact" variant="outlined" hide-details clearable
                placeholder="Started by anyone" class="axo-filter" />
      <v-select v-model="filters.period" :items="PERIODS" density="compact" variant="outlined" hide-details
                class="axo-filter axo-filter--small" />
      <v-btn v-if="anyFilter" variant="text" size="small" class="text-none" @click="clearFilters">Clear filters</v-btn>
      <v-spacer />
      <span class="text-caption axo-muted d-flex align-center ga-1">
        <v-icon size="10" :color="live.connected.value ? 'success' : 'muted'">mdi-circle</v-icon>
        {{ live.connected.value ? 'Live' : 'Updates every 10 s' }}
      </span>
    </div>
    <div v-if="filters.error || filters.run" class="d-flex ga-2 mb-3">
      <v-chip v-if="filters.error" closable size="small" @click:close="filters.error = null">Error {{ filters.error }}</v-chip>
      <v-chip v-if="filters.run" closable size="small" @click:close="filters.run = null">One choreography run</v-chip>
    </div>

    <AxoErrorAlert v-if="error" :error="error" title="The jobs couldn't load" :retry="load" class="mb-3" />

    <v-card ref="tableCard" variant="outlined" class="axo-jobs" :class="{ 'axo-jobs--scrolled': edge.left, 'axo-jobs--more': edge.right }">
      <v-table density="comfortable" class="bg-transparent">
        <thead>
          <tr>
            <th class="axo-jobs-toggle" />
            <th>Job</th>
            <th v-if="!functionId" class="d-none d-sm-table-cell">Function</th>
            <th>Status</th>
            <th class="d-none d-md-table-cell">Started by</th>
            <th>Received</th>
            <th class="d-none d-sm-table-cell text-end text-no-wrap">Total time</th>
            <th class="d-none d-xl-table-cell">Endpoint</th>
            <th v-if="showCost" class="text-end">Cost</th>
            <th class="axo-jobs-actions" />
          </tr>
        </thead>
        <tbody>
          <template v-if="loading && !rows.length">
            <tr v-for="i in 6" :key="`s${i}`"><td :colspan="columns"><v-skeleton-loader type="text" /></td></tr>
          </template>
          <tr v-else-if="!rows.length">
            <td :colspan="columns">
              <AxoEmptyState icon="mdi-briefcase-outline" :title="anyFilter ? 'No job matches these filters' : 'No jobs yet'"
                             :text="anyFilter ? 'Change or clear the filters.' : 'Jobs show here as soon as a function runs.'" />
            </td>
          </tr>
          <template v-for="row in rows" :key="row.row_id">
            <!-- a job on its own -->
            <JobsListRow v-if="row.type === 'job'" :job="row.job" :function-id="functionId" :cost="costs[row.job.job_id]"
                         :show-cost="showCost" :actions="actions" @changed="refreshSoon" />
            <!-- a choreography run: its steps' jobs inside -->
            <template v-else>
              <tr class="axo-clickable axo-run-row" @click="toggle(row.row_id)">
                <td class="axo-jobs-toggle">
                  <v-icon size="20">{{ open[row.row_id] ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
                </td>
                <td :colspan="functionId ? 1 : 2">
                  <div class="d-flex align-center ga-2 py-1">
                    <v-icon size="18" color="brand-3">mdi-sitemap-outline</v-icon>
                    <div class="min-w-0">
                      <div class="text-body-2 font-weight-medium text-truncate">
                        {{ row.run.choreography_name ?? 'Choreography' }}<span v-if="row.run.version" class="axo-muted"> v{{ row.run.version }}</span>
                      </div>
                      <div class="text-caption axo-muted">
                        Run · {{ row.shown }}<template v-if="row.steps && row.shown < row.steps"> of {{ row.steps }}</template>
                        step{{ (row.steps ?? row.shown) === 1 ? '' : 's' }} shown
                      </div>
                    </div>
                  </div>
                </td>
                <td><AxoStatusChip kind="job" :value="runStatus(row)" :label="runLabel(row)" /></td>
                <td class="d-none d-md-table-cell text-body-2">
                  <v-icon size="16" class="mr-1">{{ STARTED_BY_ICONS[row.run.started_by_type ?? 'user'] ?? 'mdi-account-outline' }}</v-icon>
                  {{ row.run.started_by_type === 'trigger' ? 'A trigger' : (row.run.started_by_name ?? row.run.started_by_id ?? '—') }}
                </td>
                <td class="text-body-2 text-no-wrap" :title="formatExactTime(ts(row.row_at))">{{ timeAgo(ts(row.row_at)) }}</td>
                <td class="d-none d-sm-table-cell text-end text-body-2">{{ row.run.duration_ms != null ? formatDuration(row.run.duration_ms) : '—' }}</td>
                <td class="d-none d-xl-table-cell" />
                <td v-if="showCost" class="text-end text-body-2 text-no-wrap">{{ runCost(row) }}</td>
                <td class="axo-jobs-actions" @click.stop>
                  <v-btn v-if="row.run.choreography_name" icon="mdi-open-in-app" size="small" variant="text" :aria-label="`Open the run of ${row.run.choreography_name}`"
                         title="Open the run" :to="`/ves/${veId}/choreographies/${encodeURIComponent(row.run.choreography_name)}/runs/${row.run.run_id}`" />
                </td>
              </tr>
              <template v-if="open[row.row_id]">
                <template v-for="part in parts(row)" :key="part.key">
                  <JobsListRow v-if="part.job" :job="part.job" :function-id="functionId" :cost="costs[part.job.job_id]"
                               :show-cost="showCost" :actions="actions" nested @changed="refreshSoon" />
                  <!-- a choreography step: the jobs of the run(s) it called -->
                  <template v-else>
                    <tr class="axo-clickable axo-call-row" @click="toggle(part.key)">
                      <td class="axo-jobs-toggle" />
                      <td :colspan="functionId ? 1 : 2">
                        <div class="d-flex align-center ga-2 py-1 pl-4">
                          <v-icon size="18">{{ open[part.key] ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
                          <v-icon size="16" color="success">mdi-layers-triple-outline</v-icon>
                          <div class="min-w-0">
                            <div class="text-body-2 text-truncate">Step {{ part.step }} · {{ part.names }}</div>
                            <div class="text-caption axo-muted">{{ part.jobs!.length }} job{{ part.jobs!.length === 1 ? '' : 's' }} of the run it called</div>
                          </div>
                        </div>
                      </td>
                      <td colspan="20" />
                    </tr>
                    <template v-if="open[part.key]">
                      <JobsListRow v-for="j in part.jobs" :key="j.job_id" :job="j" :function-id="functionId" :cost="costs[j.job_id]"
                                   :show-cost="showCost" :actions="actions" nested :depth="2" @changed="refreshSoon" />
                    </template>
                  </template>
                </template>
              </template>
            </template>
          </template>
        </tbody>
      </v-table>
    </v-card>

    <div v-if="next" class="d-flex justify-center mt-3">
      <v-btn variant="tonal" class="text-none" :loading="loadingMore" @click="loadMore">Load more</v-btn>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { JobListRow, Job } from '../utils/jobs'

// The VE's jobs, newest first, one mixed list: a job on its own is one row,
// the jobs of one choreography run are grouped under the run (open it for
// its steps; a step's job opens its attempts). Filters live in the URL;
// 50 rows a page + "Load more"; live (the VEM's jobs feed, and a re-read
// every 10 s). `functionId`: only that function's jobs (its Jobs tab).
const props = defineProps<{ functionId?: string }>()

const { id: veId, api, cp, can, vemUp } = useCurrentVe()
const functions = useFunctions()
const actions = useJobActions()
const route = useRoute()
const router = useRouter()

const PAGE = 50
const PERIODS = [
  { value: '1h', title: 'Last hour' }, { value: '24h', title: 'Last 24 hours' }, { value: '7d', title: 'Last 7 days' },
  { value: '30d', title: 'Last 30 days' }, { value: 'all', title: 'Any time' },
]
const PERIOD_S: Record<string, number> = { '1h': 3600, '24h': 86400, '7d': 604800, '30d': 2592000 }
const STARTED_BY = [
  { value: 'user', title: 'A person' }, { value: 'api_key', title: 'An API key' },
  { value: 'choreography', title: 'A choreography' }, { value: 'trigger', title: 'A bucket file' },
]

// ── filters (in the URL) ───────────────────────────────────────────────────
const q = (k: string) => (typeof route.query[k] === 'string' ? route.query[k] as string : null)
const filters = reactive({
  function: q('function'), version: q('version'), status: (q('status') ?? '').split(',').filter(Boolean),
  fault: q('fault'), by: q('by'), period: q('period') ?? 'all', error: q('error'), job: q('job'), run: q('run'),
})
const jobSearch = ref(filters.job ?? '')
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(jobSearch, (v) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { filters.job = v?.trim() || null }, 350)
})
const anyFilter = computed(() => !!(filters.function || filters.version || filters.status.length || filters.fault
  || filters.by || filters.error || filters.job || filters.run || filters.period !== 'all'))
function clearFilters() {
  Object.assign(filters, { function: null, version: null, status: [], fault: null, by: null, period: 'all', error: null,
    job: null, run: null })
  jobSearch.value = ''
}
watch(filters, () => {
  router.replace({ query: { ...route.query, function: filters.function || undefined, version: filters.version || undefined,
    status: filters.status.join(',') || undefined, fault: filters.fault || undefined, by: filters.by || undefined,
    period: filters.period === 'all' ? undefined : filters.period, error: filters.error || undefined,
    job: filters.job || undefined, run: filters.run || undefined } })
  load()
}, { deep: true })

const functionChoices = computed(() => functions.rows.value.map(f => ({ value: f.function_id, title: f.name })))
const versionChoices = computed(() => (functions.byId(props.functionId ?? '')?.versions ?? [])
  .map(v => ({ value: String(v.version), title: `v${v.version}` })))

function query(extra: Record<string, string | undefined> = {}): string {
  const p = new URLSearchParams()
  const set = (k: string, v?: string | null) => { if (v) p.set(k, v) }
  set('function_id', props.functionId ?? filters.function)
  set('version', filters.version)
  set('status', filters.status.join(','))
  set('outcome', filters.fault)
  set('started_by_type', filters.by)
  set('error', filters.error)
  set('job_id', filters.job)
  set('run_id', filters.run)
  if (PERIOD_S[filters.period]) set('since', new Date(Date.now() - PERIOD_S[filters.period]! * 1000).toISOString())
  p.set('limit', String(PAGE))
  for (const [k, v] of Object.entries(extra)) set(k, v)
  return `/jobs?${p}`
}

// ── rows ───────────────────────────────────────────────────────────────────
const rows = ref<JobListRow[]>([])
const next = ref<{ before: string; before_id: string } | null>(null)
const loading = ref(true)
const loadingMore = ref(false)
const error = ref<unknown>(null)
const open = reactive<Record<string, boolean>>({})
// One run asked for (the job page's breadcrumb): shown opened.
watch(rows, (r) => { if (filters.run) for (const row of r) if (row.type === 'run') open[row.row_id] = true })
let generation = 0

async function load() {
  if (!vemUp.value) { loading.value = false; return }
  const mine = ++generation
  loading.value = true
  error.value = null
  try {
    const page = await api<{ rows: JobListRow[]; next: typeof next.value }>(query())
    if (mine !== generation) return
    rows.value = page.rows
    next.value = page.next
    costsFor(page.rows)
  } catch (err) {
    if (mine === generation) error.value = err
  } finally {
    if (mine === generation) loading.value = false
  }
}

async function loadMore() {
  if (!next.value) return
  const mine = generation
  loadingMore.value = true
  try {
    const page = await api<{ rows: JobListRow[]; next: typeof next.value }>(query(next.value))
    if (mine !== generation) return
    const seen = new Set(rows.value.map(r => r.row_id))
    rows.value = [...rows.value, ...page.rows.filter(r => !seen.has(r.row_id))]
    next.value = page.next
    costsFor(page.rows)
  } catch (err) {
    useNotifier().error(err, "More jobs couldn't load")
  } finally {
    loadingMore.value = false
  }
}

/** The newest page again, merged on top (rows already loaded stay). */
async function refresh() {
  if (!vemUp.value || loading.value) return
  const mine = generation
  try {
    const page = await api<{ rows: JobListRow[]; next: typeof next.value }>(query())
    if (mine !== generation) return
    const fresh = new Map(page.rows.map(r => [r.row_id, r]))
    const kept = rows.value.map(r => fresh.get(r.row_id) ?? r)
    const known = new Set(rows.value.map(r => r.row_id))
    rows.value = [...page.rows.filter(r => !known.has(r.row_id)), ...kept]
    if (!rows.value.length) next.value = page.next
    costsFor(page.rows)
  } catch { /* the next tick tries again */ }
}
let soon: ReturnType<typeof setTimeout> | null = null
function refreshSoon() {
  if (soon) return
  soon = setTimeout(() => { soon = null; refresh() }, 400)
}
onBeforeUnmount(() => { if (soon) clearTimeout(soon) })

watch([() => props.functionId, vemUp], () => { rows.value = []; load() })
onMounted(() => { functions.load(); load() })
usePolling(() => (rows.value.length ? refresh() : undefined), 10_000)

// A job changed: shown in place at once (rows not loaded come with the next read).
const live = useVeLive(() => (vemUp.value ? 'jobs' : null), (msg: Partial<Job>) => {
  if (!msg?.job_id) return
  let found = false
  rows.value = rows.value.map((r) => {
    if (r.type === 'job' && r.job.job_id === msg.job_id) { found = true; return { ...r, job: { ...r.job, ...msg } } }
    if (r.type === 'run' && r.jobs.some(j => j.job_id === msg.job_id)) {
      found = true
      return { ...r, jobs: r.jobs.map(j => (j.job_id === msg.job_id ? { ...j, ...msg } : j)) }
    }
    return r
  })
  if (!found) refreshSoon()
})

const toggle = (rowId: string) => { open[rowId] = !open[rowId] }

/** A run's jobs as shown: its own steps' jobs, and per choreography step one
 *  line holding the jobs of the run(s) that step called (deeper calls too). */
interface RunPart { key: string; job?: Job; step?: string; names?: string; jobs?: Job[] }
function parts(row: Extract<JobListRow, { type: 'run' }>): RunPart[] {
  const out: RunPart[] = []
  const calls = new Map<string, RunPart & { jobs: Job[]; seen: Set<string> }>()
  for (const j of row.jobs) {
    const c = j.choreography
    if (!c || c.run_id === row.run.run_id || !c.root_step) { out.push({ key: j.job_id, job: j }); continue }
    let call = calls.get(c.root_step)
    if (!call) {
      call = { key: `${row.row_id}:${c.root_step}`, step: c.root_step, jobs: [], seen: new Set() }
      calls.set(c.root_step, call)
      out.push(call)
    }
    call.seen.add(c.choreography_name ?? 'a choreography')
    call.names = [...call.seen].join(', ')
    call.jobs.push(j)
  }
  return out
}

function runStatus(row: Extract<JobListRow, { type: 'run' }>): string {
  if (row.jobs.some(j => isActive(j))) return 'RUNNING'
  if (row.jobs.some(j => j.status === 'FAILED')) return 'FAILED'
  if (row.jobs.some(j => j.status === 'CANCELLED')) return 'CANCELLED'
  return 'COMPLETED'
}
function runLabel(row: Extract<JobListRow, { type: 'run' }>): string | undefined {
  const s = row.run.status
  return s === 'completed_with_errors' ? 'Completed with errors' : s ? s[0]!.toUpperCase() + s.slice(1).replace(/_/g, ' ') : undefined
}

// ── sideways scrolling: which edges have more to show ──────────────────────
const tableCard = ref<{ $el: HTMLElement } | null>(null)
const edge = reactive({ left: false, right: false })
let scroller: HTMLElement | null = null
let resizes: ResizeObserver | null = null
function measureEdges() {
  if (!scroller) return
  edge.left = scroller.scrollLeft > 0
  edge.right = scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1
}
onMounted(() => {
  scroller = tableCard.value?.$el.querySelector<HTMLElement>('.v-table__wrapper') ?? null
  if (!scroller) return
  scroller.addEventListener('scroll', measureEdges, { passive: true })
  resizes = new ResizeObserver(measureEdges)
  resizes.observe(scroller)
  const table = scroller.querySelector('table')
  if (table) resizes.observe(table)
  measureEdges()
})
onBeforeUnmount(() => { scroller?.removeEventListener('scroll', measureEdges); resizes?.disconnect() })

// ── costs (Owner / Admin), every attempt together ──────────────────────────
const showCost = computed(() => can('admin'))
const costs = ref<Record<string, number>>({})
async function costsFor(page: JobListRow[]) {
  if (!showCost.value) return
  const ids = page.flatMap(r => (r.type === 'job' ? [r.job] : r.jobs)).filter(j => !isActive(j)).map(j => j.job_id)
    .filter(jid => costs.value[jid] === undefined)
  if (!ids.length) return
  try {
    const answer = await cp<{ jobs: Record<string, { cost_ucents: number }> }>(`/costs/jobs?job_ids=${ids.join(',')}`)
    const add: Record<string, number> = {}
    for (const jid of ids) add[jid] = answer.jobs[jid]?.cost_ucents ?? -1
    costs.value = { ...costs.value, ...add }
  } catch { /* the column shows — */ }
}
function runCost(row: Extract<JobListRow, { type: 'run' }>): string {
  const known = row.jobs.map(j => costs.value[j.job_id]).filter((c): c is number => c != null && c >= 0)
  return known.length ? formatUcents(known.reduce((a, b) => a + b, 0)) : '—'
}

const columns = computed(() => 8 + (props.functionId ? 0 : 1) + (showCost.value ? 1 : 0))
</script>

<style scoped>
.axo-filter { flex: 0 1 200px; min-width: 150px; }
.axo-filter--small { flex-basis: 150px; min-width: 120px; }
.axo-filter--id { flex-basis: 180px; }
/* Wider than its box (a long cost, a small window): the table scrolls
   sideways inside it, the toggle + Job columns stay put, and a shadow shows
   there is more on that side. The page itself never scrolls sideways. */
.axo-jobs { --axo-jobs-bg: rgb(var(--v-theme-background)); }
.axo-jobs :deep(.v-table__wrapper) { overflow-x: auto; }
.axo-jobs :deep(tr > :nth-child(-n + 2)) { position: sticky; z-index: 1; background-color: var(--axo-jobs-bg); }
.axo-jobs :deep(tr > :nth-child(1)) { left: 0; }
.axo-jobs :deep(tr > :nth-child(2)) { left: 36px; transition: box-shadow .15s; }
.axo-jobs--scrolled :deep(tr > :nth-child(2)) { box-shadow: 6px 0 6px -4px rgba(0, 0, 0, 0.18); }
.axo-jobs::after { content: ''; position: absolute; top: 0; right: 0; bottom: 0; width: 14px; pointer-events: none; opacity: 0;
  background: linear-gradient(to left, rgba(0, 0, 0, 0.12), transparent); transition: opacity .15s; z-index: 2; }
.axo-jobs--more::after { opacity: 1; }
.axo-jobs-toggle { width: 36px; padding-right: 0 !important; }
.axo-jobs-actions { width: 48px; padding-inline: 4px !important; }
.axo-call-row > td { background-image: linear-gradient(rgba(var(--v-theme-success), 0.04), rgba(var(--v-theme-success), 0.04)); }
.axo-run-row > td { background-image: linear-gradient(rgba(var(--v-theme-brand-3), 0.04), rgba(var(--v-theme-brand-3), 0.04)); }
@media (max-width: 600px) { .axo-filter, .axo-filter--small, .axo-filter--id { flex-basis: 100%; } }
</style>
