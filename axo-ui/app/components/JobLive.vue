<template>
  <v-card variant="outlined" class="pa-4">
    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <span class="text-subtitle-1 font-weight-medium">This run</span>
      <AxoStatusChip v-if="job" kind="job" :value="job.status" />
      <span v-if="job && attemptText(job)" class="text-caption axo-muted">{{ attemptText(job) }}</span>
      <span v-if="job" class="text-caption axo-muted">· {{ elapsed }}</span>
      <v-spacer />
      <v-btn v-if="job && actions.canCancel(job)" size="small" variant="text" color="error" class="text-none" prepend-icon="mdi-cancel"
             :loading="actions.busy.value === `cancel:${jobId}`" @click="cancel">Cancel</v-btn>
      <v-btn size="small" variant="text" class="text-none" append-icon="mdi-arrow-right" :to="`/ves/${id}/jobs/${jobId}`">Open the job</v-btn>
    </div>
    <div class="text-caption axo-mono axo-muted mb-3">{{ jobId }}</div>

    <AxoErrorAlert v-if="error" :error="error" title="The job couldn't be followed" :retry="load" />
    <template v-else-if="job">
      <div v-if="isActive(job)" class="d-flex align-center ga-2 text-body-2">
        <v-progress-circular indeterminate size="18" width="2" color="info" />
        {{ job.status === 'RUNNING' ? 'Running…' : job.status === 'WAITING_RETRY' ? 'An attempt failed; trying again…' : 'Waiting for a free runner…' }}
      </div>
      <template v-else-if="job.status === 'COMPLETED'">
        <AxoCode v-if="result !== null" :model-value="result" language="json" readonly min-height="40px" max-height="320px" label="Result" />
        <div v-else-if="resultMissing" class="text-body-2 axo-muted">{{ resultMissing }}</div>
        <v-skeleton-loader v-else type="paragraph" />
      </template>
      <div v-else>
        <div class="d-flex align-center flex-wrap ga-2 mb-1">
          <AxoErrorName v-if="job.error?.name" :name="job.error.name" :code="job.error.code ?? undefined" />
          <span v-if="job.error?.fault" class="text-caption">{{ FAULTS[job.error.fault]?.label }}</span>
        </div>
        <div class="text-body-2">{{ job.error?.message ?? (job.status === 'CANCELLED' ? 'Cancelled.' : 'It failed.') }}</div>
        <div class="text-caption axo-muted mt-1">The job page has every attempt{{ can('developer') ? ' and the traceback' : '' }}.</div>
      </div>
    </template>
    <v-skeleton-loader v-else type="paragraph" />
  </v-card>
</template>

<script setup lang="ts">
import type { Job } from '~/utils/jobs'

// A job just started from the run form, followed live until it ends: then
// its result (JSON) or its error. `finished` fires once, when it ends.
const props = defineProps<{ jobId: string }>()
const emit = defineEmits<{ (e: 'finished', job: Job): void }>()
const { id, api, can } = useCurrentVe()
const actions = useJobActions()

const job = ref<Job | null>(null)
const error = ref<unknown>(null)
const result = ref<string | null>(null)
const resultMissing = ref<string | null>(null)
let done = false

async function load() {
  try {
    const fresh = await api<Job>(`/jobs/${props.jobId}`)
    job.value = fresh
    error.value = null
    if (!isActive(fresh) && !done) {
      done = true
      emit('finished', fresh)
      if (fresh.status === 'COMPLETED') loadResult()
    }
  } catch (err) {
    if ((err as any)?.status !== 404) error.value = err      // a new job may not be recorded for a moment
  }
}
async function loadResult(tries = 0) {
  try {
    result.value = prettyJson(await api(`/jobs/${props.jobId}/result`), 50_000).text
  } catch (err) {
    if (tries < 5) setTimeout(() => loadResult(tries + 1), 1000)        // still being saved
    else resultMissing.value = (err as any)?.message ?? "The result isn't kept."
  }
}

let timer: ReturnType<typeof setInterval> | null = null
const now = ref(Date.now())
onMounted(() => {
  load()
  timer = setInterval(() => { now.value = Date.now(); if (!done) load() }, 1500)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
watch(() => props.jobId, () => { done = false; job.value = null; result.value = null; resultMissing.value = null; load() })
useVeLive(() => `jobs/${props.jobId}`, () => { if (!done) load() })

const elapsed = computed(() => {
  const j = job.value
  if (!j) return ''
  if (!isActive(j)) return j.duration_ms != null ? `took ${formatDuration(j.duration_ms)}` : ''
  return j.created_at ? formatDuration(now.value - Date.parse(j.created_at)) : ''
})
async function cancel() { if (job.value && await actions.cancel(job.value)) load() }
</script>
