<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <div class="text-h6 d-flex align-center ga-1">Endpoints
          <AxoHelp title="Endpoints" text="The machines your VE runs jobs on. Each one runs functions and objects; one of them leads (it keeps the order of object calls and spreads the work). The VE keeps its target number running and replaces one that fails." />
        </div>
        <div v-if="view" class="text-body-2 axo-muted">
          {{ running }} running · target {{ view.settings.target_endpoints }} · at least {{ view.settings.min_endpoints }}
          · plan maximum {{ view.plan_limits.max_endpoints }}
        </div>
      </div>
      <v-spacer />
      <v-switch v-model="showRemoved" color="primary" inset hide-details density="compact" label="Show removed" class="flex-grow-0" />
      <AxoBtn v-if="can('admin')" prepend-icon="mdi-plus" :disabled="!vemUp || !view || room < 1" @click="adding = true">
        Add endpoints
      </AxoBtn>
    </div>

    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: its endpoints show here once it runs (a sleeping VE wakes with its next job).
    </v-alert>
    <AxoErrorAlert v-if="error" :error="error" title="The endpoints couldn't load" :retry="load" class="mb-4" />

    <AxoDataTable v-if="vemUp" :headers="headers" :items="shown" item-value="endpoint_id" :loading="loading" :searchable="false"
                  :row-to="(e) => `/ves/${id}/endpoints/${e.endpoint_id}`" :pending="(e) => moving(e)"
                  :sort-by="[{ key: 'state', order: 'asc' }]" empty-icon="mdi-server-outline" empty-title="No endpoints"
                  :empty-text="can('admin') ? 'Add one to run jobs.' : 'The VE has no endpoints right now.'">
      <template #item.endpoint_id="{ item }">
        <div class="d-flex align-center flex-wrap ga-1 py-2">
          <span class="axo-mono text-body-2 text-no-wrap">{{ item.endpoint_id }}</span>
          <v-chip v-if="isLeader(item)" size="x-small" color="primary" variant="tonal" prepend-icon="mdi-crown-outline">Leader</v-chip>
        </div>
      </template>
      <template #item.state="{ item }">
        <AxoStatusChip kind="endpoint" :value="endpointState(item)" :reason="item.lifecycle_reason" />
      </template>
      <template #item.size="{ item }">
        <span v-if="item.cpu_cores">{{ item.cpu_cores }} CPU · {{ formatMemoryMb(item.memory_mb) }}</span>
        <span v-else class="axo-muted">—</span>
      </template>
      <template #item.use="{ item }">
        <div v-if="stats[item.endpoint_id]?.available" class="axo-use">
          <div class="d-flex align-center ga-2"><span class="axo-use-label">CPU</span>
            <v-progress-linear :model-value="cpuOfSize(stats[item.endpoint_id], item.cpu_cores) ?? 0" color="brand-2" rounded height="6" />
            <span class="axo-use-value">{{ formatPercent(cpuOfSize(stats[item.endpoint_id], item.cpu_cores)) }}</span></div>
          <div class="d-flex align-center ga-2"><span class="axo-use-label">RAM</span>
            <v-progress-linear :model-value="memoryOfSize(stats[item.endpoint_id]) ?? 0" color="brand-3" rounded height="6" />
            <span class="axo-use-value">{{ formatPercent(memoryOfSize(stats[item.endpoint_id])) }}</span></div>
        </div>
        <span v-else class="text-caption axo-muted">{{ endpointState(item) === 'Running' ? 'No live figures' : '—' }}</span>
      </template>
      <template #item.jobs="{ item }">
        <template v-if="item.metrics">
          {{ formatNumber(item.metrics.completed) }}
          <span v-if="item.metrics.failed" class="text-error">· {{ formatNumber(item.metrics.failed) }} failed</span>
        </template>
        <span v-else class="axo-muted">—</span>
      </template>
      <template #item.queue="{ item }">
        <span v-if="item.metrics">{{ item.metrics.queue_depth }}</span><span v-else class="axo-muted">—</span>
      </template>
      <template #item.uptime="{ item }">
        <span v-if="item.uptime_ms && endpointState(item) === 'Running'">{{ formatDuration(item.uptime_ms) }}</span>
        <span v-else class="text-caption axo-muted" :title="item.lifecycle_since ?? ''">
          {{ item.lifecycle_since ? `since ${timeAgo(Date.parse(item.lifecycle_since) / 1000)}` : '—' }}
        </span>
      </template>
      <template #item.actions="{ item }">
        <v-menu v-if="can('admin')" location="bottom end">
          <template #activator="{ props: menu }">
            <v-btn v-bind="menu" icon="mdi-dots-vertical" variant="text" size="small" :aria-label="`Actions for ${item.endpoint_id}`"
                   :loading="actions.busy.value?.endsWith(item.endpoint_id)" @click.stop />
          </template>
          <v-list density="compact" rounded="lg" min-width="200">
            <v-list-item prepend-icon="mdi-restart" title="Restart" :disabled="endpointState(item) !== 'Running'"
                         @click="act(actions.restart(item))" />
            <v-list-item prepend-icon="mdi-minus-circle-outline" title="Remove" base-color="error"
                         :disabled="!isPresent(item) || atMinimum" :subtitle="isPresent(item) && atMinimum ? 'The VE is at its minimum' : undefined"
                         @click="act(actions.remove(item, view?.settings.target_endpoints ?? present.length))" />
            <v-list-item v-if="item.purge_eligible" prepend-icon="mdi-delete-outline" title="Forget" base-color="error"
                         @click="act(actions.purge(item))" />
          </v-list>
        </v-menu>
      </template>
    </AxoDataTable>

    <EndpointAddDialog v-model="adding" :view="view" :present="present" @added="load" />
  </div>
</template>

<script setup lang="ts">
// The VE's endpoints (the VEM's GET /endpoints), live: status changes and
// container CPU / memory come over the VEM's endpoints feed, the list is
// also read again every 10 s. Admins add endpoints (target + N), restart,
// remove (target − 1, that one drains) and forget removed ones.
const { id, ve, can, vemUp, api } = useCurrentVe()
const actions = useEndpointActions()
useHead(() => ({ title: `Endpoints · ${ve.value?.name ?? 'VE'} · axo` }))

const rows = ref<EndpointRow[]>([])
const view = ref<{ settings: Record<string, any>; plan_limits: Record<string, any> } | null>(null)
const stats = ref<Record<string, EndpointStats>>({})
const loading = ref(true)
const error = ref<unknown>(null)
const adding = ref(false)
const route = useRoute()
const router = useRouter()
const showRemoved = ref(route.query.removed === '1')
watch(showRemoved, v => router.replace({ query: { ...route.query, removed: v ? '1' : undefined } }))

let generation = 0
async function load() {
  if (!vemUp.value) { loading.value = false; return }
  const mine = ++generation
  error.value = null
  try {
    const [list, v] = await Promise.all([api<EndpointRow[]>('/endpoints?include_removed=true'), api('/ve')])
    if (mine !== generation) return
    rows.value = list
    view.value = v
  } catch (err) {
    if (mine === generation) error.value = err
  } finally {
    if (mine === generation) loading.value = false
  }
}
watch([id, vemUp], () => { rows.value = []; stats.value = {}; loading.value = true; load() })
usePolling(load, 10_000)

// Live: a status change reloads (shortly, once for a burst); stats are kept.
let reloadSoon: ReturnType<typeof setTimeout> | null = null
useVeLive(() => (vemUp.value ? 'endpoints' : null), (msg) => {
  if (msg && typeof msg.available === 'boolean') stats.value = { ...stats.value, [msg.endpoint_id]: msg }
  else if (msg?.endpoint_id && !reloadSoon) reloadSoon = setTimeout(() => { reloadSoon = null; load() }, 500)
})
onBeforeUnmount(() => { if (reloadSoon) clearTimeout(reloadSoon) })

async function act(done: Promise<boolean>) { if (await done) load() }

const ORDER = ['Running', 'Starting', 'Creating', 'Draining', 'Removing', 'Sleeping', 'Failed', 'Unreachable', 'Stopped', 'Removed']
const shown = computed(() => (showRemoved.value ? rows.value : rows.value.filter(e => !isGone(e) || e.unreachable_since)))
const present = computed(() => rows.value.filter(isPresent))
const running = computed(() => rows.value.filter(e => endpointState(e) === 'Running').length)
const room = computed(() => (view.value?.plan_limits.max_endpoints ?? 0) - present.value.length)
const atMinimum = computed(() => present.value.length <= (view.value?.settings.min_endpoints ?? 0))
const moving = (e: EndpointRow) => ['Creating', 'Starting', 'Draining', 'Removing'].includes(endpointState(e))

// Wide screens only (CSS, so the server and the browser render the same).
const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const headers = [
  { title: 'Endpoint', key: 'endpoint_id' },
  { title: 'Status', key: 'state', value: (e: EndpointRow) => ORDER.indexOf(endpointState(e)) },
  { title: 'Size', key: 'size', sortable: false, ...WIDE },
  { title: 'Use now', key: 'use', sortable: false, width: 190, ...WIDE },
  { title: 'Jobs done', key: 'jobs', value: (e: EndpointRow) => e.metrics?.completed ?? -1 },
  { title: 'Queued', key: 'queue', value: (e: EndpointRow) => e.metrics?.queue_depth ?? -1 },
  { title: 'Up for', key: 'uptime', value: (e: EndpointRow) => e.uptime_ms ?? -1, ...WIDE },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 56 },
]
</script>

<style scoped>
.axo-use { min-width: 150px; }
.axo-use-label { width: 30px; font-size: 0.72rem; opacity: 0.7; }
.axo-use-value { width: 38px; text-align: right; font-size: 0.78rem; font-variant-numeric: tabular-nums; }
</style>
