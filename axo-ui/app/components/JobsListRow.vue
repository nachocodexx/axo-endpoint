<template>
  <tr class="axo-clickable" :class="{ 'axo-nested': nested }" @click="open">
    <td class="axo-jobs-toggle" />
    <td>
      <div class="d-flex align-center ga-2 py-1" :style="nested ? { paddingLeft: `${16 * (depth ?? 1)}px` } : undefined">
        <div class="min-w-0">
          <div class="axo-mono text-body-2 text-truncate" style="max-width: 120px" :title="job.job_id">{{ job.job_id }}</div>
          <div v-if="nested && job.choreography" class="text-caption axo-muted">Step {{ job.choreography.step }}</div>
          <div v-else-if="functionId" class="text-caption axo-muted">v{{ job.function_version }}</div>
          <div v-else class="text-caption axo-muted d-sm-none text-truncate">{{ jobSubject(job) }} v{{ job.function_version }}</div>
        </div>
      </div>
    </td>
    <td v-if="!functionId" class="d-none d-sm-table-cell">
      <NuxtLink v-if="job.kind === 'function'" :to="`/ves/${id}/functions/${job.function_id}`" class="axo-link text-body-2" @click.stop>
        {{ jobSubject(job) }}
      </NuxtLink>
      <NuxtLink v-else-if="job.kind === 'object' && job.object_name" :to="`/ves/${id}/objects/${encodeURIComponent(job.object_name)}`"
                class="axo-link text-body-2" @click.stop>{{ jobSubject(job) }}</NuxtLink>
      <span v-else class="text-body-2">{{ jobSubject(job) }}</span>
      <span class="text-caption axo-muted"> v{{ job.function_version }}</span>
    </td>
    <td>
      <div class="d-flex flex-column align-start py-1">
        <AxoStatusChip kind="job" :value="job.status" />
        <span v-if="attempts" class="text-caption axo-muted mt-1">{{ attempts }}</span>
        <span v-if="job.status === 'WAITING_RETRY' && job.next_attempt_at" class="text-caption axo-muted">
          next {{ timeAgo(ts(job.next_attempt_at)) }}
        </span>
        <span v-else-if="job.error && !isActive(job)" class="text-caption text-error text-truncate" style="max-width: 160px"
              :title="job.error.message ?? ''">{{ job.error.name }}</span>
      </div>
    </td>
    <td class="d-none d-md-table-cell text-body-2">
      <span class="d-inline-flex align-center ga-1">
        <v-icon size="16">{{ STARTED_BY_ICONS[job.started_by_type ?? 'user'] ?? 'mdi-account-outline' }}</v-icon>
        <span class="text-truncate" style="max-width: 140px" :title="startedByText(job)">{{ startedByText(job) }}</span>
      </span>
    </td>
    <td class="text-body-2 text-no-wrap" :title="formatExactTime(ts(job.created_at))">{{ timeAgo(ts(job.created_at)) }}</td>
    <td class="d-none d-sm-table-cell text-end text-body-2 text-no-wrap">
      <template v-if="job.duration_ms != null && !isActive(job)">{{ formatDuration(job.duration_ms) }}</template>
      <span v-else-if="isActive(job) && job.created_at" class="axo-muted">{{ formatDuration(now - Date.parse(job.created_at)) }}…</span>
      <span v-else class="axo-muted">—</span>
    </td>
    <td class="d-none d-xl-table-cell">
      <span v-if="job.endpoint_id" class="axo-mono text-caption text-truncate d-inline-block" style="max-width: 120px" :title="job.endpoint_id">{{ job.endpoint_id }}</span>
      <span v-else class="axo-muted">—</span>
    </td>
    <td v-if="showCost" class="text-end text-body-2 text-no-wrap">
      {{ cost != null && cost >= 0 ? formatUcents(cost) : '—' }}
    </td>
    <td class="axo-jobs-actions" @click.stop>
      <v-menu location="bottom end">
        <template #activator="{ props: menu }">
          <v-btn v-bind="menu" icon="mdi-dots-vertical" variant="text" size="small" :aria-label="`Actions for job ${job.job_id}`"
                 :loading="actions.busy.value?.endsWith(job.job_id)" />
        </template>
        <v-list density="compact" rounded="lg" min-width="200">
          <v-list-item prepend-icon="mdi-open-in-app" title="Open" :to="`/ves/${id}/jobs/${job.job_id}`" />
          <v-list-item v-if="actions.canCancel(job)" prepend-icon="mdi-cancel" title="Cancel" base-color="error"
                       @click="act(actions.cancel(job))" />
          <v-list-item v-if="actions.canRetry(job)" prepend-icon="mdi-refresh" title="Retry" subtitle="A new attempt of this job"
                       @click="act(actions.retry(job))" />
          <v-list-item v-if="actions.hasResult(job)" prepend-icon="mdi-download" title="Download result"
                       @click="actions.downloadResult(job)" />
        </v-list>
      </v-menu>
    </td>
  </tr>
</template>

<script setup lang="ts">
import type { Job } from '../utils/jobs'

// One job in the jobs list (on its own, or a step inside a choreography run).
const props = defineProps<{
  job: Job
  functionId?: string
  cost?: number
  showCost: boolean
  nested?: boolean
  /** How deep it is nested (2 = inside a choreography step's run). */
  depth?: number
  actions: ReturnType<typeof useJobActions>
}>()
const emit = defineEmits<{ (e: 'changed'): void }>()
const { id } = useCurrentVe()
const attempts = computed(() => attemptText(props.job))
const open = () => navigateTo(`/ves/${id.value}/jobs/${props.job.job_id}`)
async function act(done: Promise<boolean>) { if (await done) emit('changed') }

// A running job's time ticks (only while it runs).
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null
function tick(active: boolean) {
  if (timer) clearInterval(timer)
  timer = active ? setInterval(() => { now.value = Date.now() }, 1000) : null
}
onMounted(() => watch(() => isActive(props.job), tick, { immediate: true }))
onBeforeUnmount(() => tick(false))
</script>

<style scoped>
.axo-nested td { background-image: linear-gradient(rgba(var(--v-theme-brand-3), 0.02), rgba(var(--v-theme-brand-3), 0.02)); }
.axo-jobs-toggle { width: 36px; padding-right: 0 !important; }
.axo-jobs-actions { width: 48px; padding-inline: 4px !important; }
</style>
