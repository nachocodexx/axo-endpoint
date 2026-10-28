<template>
  <div>
    <template v-if="ve">
    <!-- Last 24 hours -->
    <v-row density="compact" class="mb-2">
      <v-col cols="12" sm="4">
        <AxoStatCard label="Jobs today" icon="mdi-play-circle-outline" :loading="figuresLoading" :value="formatNumber(jobs)"
                     caption="Last 24 hours" :to="`/ves/${id}/monitoring`" help="Job attempts that finished in the last 24 hours." />
      </v-col>
      <v-col cols="12" sm="4">
        <AxoStatCard label="Success rate" icon="mdi-check-decagram-outline" color="success" :loading="figuresLoading"
                     :value="successRate == null ? null : successRate.toFixed(1)" unit="%" caption="Last 24 hours"
                     :to="`/ves/${id}/monitoring`" help="Share of those jobs that completed." />
      </v-col>
      <v-col cols="12" sm="4">
        <AxoStatCard label="Job time (p95)" icon="mdi-timer-outline" color="brand-3" :loading="figuresLoading"
                     :value="formatMs(p95)" caption="Last 24 hours" :to="`/ves/${id}/monitoring`"
                     help="95 % of completed jobs took at most this long, from arriving at the platform to their result." />
      </v-col>
    </v-row>

    <v-row density="compact">
      <v-col cols="12" lg="7">
        <AxoPanel title="Details" icon="mdi-information-outline">
          <AxoKeyValue :columns="2" :items="details">
            <template #Owner>{{ ve.owner.kind === 'team' ? `Team ${ve.owner.name ?? ''}` : ve.owner.name }}</template>
            <template #ID><AxoCopy :text="ve.ve_id" /></template>
          </AxoKeyValue>
        </AxoPanel>
      </v-col>

      <v-col cols="12" lg="5">
        <AxoPanel title="Right now" icon="mdi-pulse" help="What the VE's manager reports: its endpoints by state and anything it couldn't do.">
          <template #actions>
            <span v-if="statusAt" class="text-caption axo-muted">{{ timeAgo(statusAt / 1000) }}</span>
          </template>
          <div v-if="asleep" class="text-body-2 axo-muted">
            {{ DOWN_TEXT[ve.lifecycle] ?? `${ve.lifecycle}: its manager answers once it runs.` }}
          </div>
          <AxoErrorAlert v-else-if="statusError" :error="statusError" title="The VE didn't answer" :retry="loadStatus" />
          <AxoSkeleton v-else-if="!status" :lines="3" />
          <template v-else>
            <div class="d-flex flex-wrap ga-2 mb-3">
              <v-chip v-for="(n, state) in status.endpoints" :key="state" size="small" variant="tonal"
                      :color="statusLook('endpoint', cap(String(state))).color">
                {{ n }} {{ String(state) }}
              </v-chip>
              <span v-if="!Object.keys(status.endpoints || {}).length" class="text-body-2 axo-muted">No endpoints yet.</span>
            </div>
            <div class="text-body-2">
              Target {{ status.target_endpoints }} endpoint{{ status.target_endpoints === 1 ? '' : 's' }},
              at least {{ status.min_endpoints }}.
              <span v-if="status.idle_since" class="axo-muted">Idle since {{ formatDateTime(Date.parse(status.idle_since) / 1000) }}.</span>
              <NuxtLink :to="`/ves/${id}/endpoints`" class="text-primary text-decoration-none ml-1">See the endpoints</NuxtLink>
            </div>
            <div v-if="status.restore && (status.restore.running || status.restore.failed?.length)" class="mt-3">
              <div class="text-body-2 mb-1">Bringing back functions: {{ status.restore.restored }} of {{ status.restore.total }}</div>
              <v-progress-linear :model-value="status.restore.total ? 100 * status.restore.restored / status.restore.total : 0"
                                 color="primary" rounded height="6" :indeterminate="!status.restore.total && status.restore.running" />
              <div v-for="f in status.restore.failed" :key="JSON.stringify(f)" class="text-caption text-error mt-1">
                {{ f.name ?? f.function_id }}: {{ f.error ?? 'failed' }}
              </div>
            </div>
            <v-alert v-for="(e, i) in status.errors" :key="i" type="warning" variant="tonal" density="compact" class="mt-3">
              {{ typeof e === 'string' ? e : (e.message ?? JSON.stringify(e)) }}
            </v-alert>
          </template>
        </AxoPanel>
        <VeStored class="mt-4" />
      </v-col>
    </v-row>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { SummaryAnswer } from 'axo-ui-kit/app/types/cp'

// A VE at a glance: last 24 hours of jobs, its details, and what its VEM
// says right now (GET /ve through the front door, every 10 s).
const { id, ve, vemUp, api, cp } = useCurrentVe()

// ── figures ────────────────────────────────────────────────────────────────
const summary = ref<SummaryAnswer | null>(null)
const figuresLoading = ref(true)
async function loadFigures() {
  const end = Date.now() / 1000
  try {
    summary.value = await cp<SummaryAnswer>(`/stats/summary?metric=job.total_ms&group_by=outcome&statistics=count,p95&start=${end - 86400}&end=${end}`)
  } catch { summary.value = null } finally { figuresLoading.value = false }
}
const jobs = computed(() => (summary.value?.groups ?? []).reduce((a, g) => a + (g.values.count ?? 0), 0))
const successRate = computed(() => {
  const ok = summary.value?.groups.find(g => g.group === 'completed')?.values.count ?? 0
  return jobs.value ? (100 * ok) / jobs.value : null
})
const p95 = computed(() => summary.value?.groups.find(g => g.group === 'completed')?.values.p95 ?? null)

// ── details ────────────────────────────────────────────────────────────────
const details = computed(() => {
  const v = ve.value!
  const r = v.rental
  return [
    { label: 'Plan', value: v.plan_id[0]!.toUpperCase() + v.plan_id.slice(1) },
    { label: 'Owner' },
    { label: 'Endpoint size', value: `${v.endpoint_size.name} · ${v.endpoint_size.cpu_cores} CPU · ${formatMemoryMb(v.endpoint_size.memory_mb)}` },
    { label: 'Quota', value: `${v.quota.cpu_cores} CPU · ${formatMemoryMb(v.quota.memory_mb)}`,
      help: 'The most CPU and memory all its endpoints may use together.' },
    { label: 'Endpoints', value: `${v.endpoints_running} running · target ${v.target_endpoints} · at least ${v.min_endpoints}` },
    { label: 'Created', value: formatDateTime(v.created_at) },
    { label: 'This period', value: r.period_start ? `${formatDate(r.period_start)} – ${formatDate(r.period_end)}` : '—' },
    { label: 'Fee per period', value: formatMoney(r.period_fee_cents, { free: true }) },
    ...(r.period_fee_cents ? [{ label: 'Budget', value: r.budget_cents != null ? `${formatMoney(r.budget_cents)} per period` : 'None',
      help: 'Notices at 80 % and 100 % of the bill; jobs never stop because of it.' }] : []),
    { label: 'ID' },
  ]
})

// ── right now ──────────────────────────────────────────────────────────────
const status = ref<any>(null)
const statusError = ref<unknown>(null)
const statusAt = ref<number | null>(null)
const asleep = computed(() => !vemUp.value)
const DOWN_TEXT: Record<string, string> = {
  Sleeping: 'Sleeping: the next job wakes it (that job waits for the start).',
  Stopped: 'Stopped: start it to run jobs again.',
  Failed: 'It failed; see the reason above.',
}
async function loadStatus() {
  if (asleep.value) return
  try {
    status.value = await api('/ve')
    statusError.value = null
    statusAt.value = Date.now()
  } catch (err) { if (!status.value) statusError.value = err }
}
const cap = (s: string) => s[0]!.toUpperCase() + s.slice(1)

usePolling(loadStatus, 10_000)
onMounted(loadFigures)
watch(() => ve.value?.lifecycle, (now, before) => { if (now !== before) loadStatus() })
</script>
