<template>
  <v-card variant="outlined" class="axo-attempt" :class="`axo-attempt--${tone}`">
    <div class="d-flex align-center flex-wrap ga-2 pa-3 axo-clickable" role="button" :aria-expanded="opened"
         @click="opened = !opened">
      <v-icon :color="tone" :icon="icon" />
      <span class="text-body-1 font-weight-medium">Attempt {{ attempt.attempt }}</span>
      <span class="text-body-2">{{ outcomeText }}</span>
      <v-chip v-if="fault && attempt.outcome !== 'completed'" size="x-small" variant="tonal" :color="FAULTS[fault]!.color">
        {{ FAULTS[fault]!.label }}
      </v-chip>
      <v-spacer />
      <span class="text-caption axo-muted">{{ reasonText }}</span>
      <span v-if="cost != null" class="text-caption">{{ formatUcents(cost) }}</span>
      <v-icon size="20">{{ opened ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
    </div>

    <div class="px-3 pb-3">
      <JobTimeBar :attempt="attempt" />
      <div class="d-flex flex-wrap ga-4 mt-2 text-caption axo-muted">
        <span v-if="attempt.received_at">Received {{ formatExactTime(ts(attempt.received_at)) }}</span>
        <span v-if="attempt.started_at">Started {{ formatTime(ts(attempt.started_at)) }}</span>
        <span v-if="attempt.finished_at">Finished {{ formatTime(ts(attempt.finished_at)) }}</span>
        <span v-if="attempt.execution_ms != null">Ran {{ formatMs(attempt.execution_ms) }}</span>
        <span v-if="attempt.endpoint_id">on <span class="axo-mono">{{ attempt.endpoint_id }}</span></span>
      </div>
    </div>

    <v-expand-transition>
      <div v-if="opened" class="px-3 pb-3">
        <v-divider class="mb-3" />
        <template v-if="attempt.failure">
          <div class="d-flex align-center flex-wrap ga-2 mb-1">
            <AxoErrorName v-if="attempt.failure.error_class" :name="attempt.failure.error_class"
                          :code="attempt.failure.error_code ?? undefined" :color="fault === 'code' ? 'error' : 'warning'" />
            <span v-if="attempt.failure.error_type" class="text-caption axo-mono">{{ attempt.failure.error_type }}</span>
            <span v-if="attempt.failure.component" class="text-caption axo-muted">in {{ attempt.failure.component }}</span>
          </div>
          <div class="text-body-2 mb-1">{{ attempt.failure.message }}</div>
          <div v-if="fault" class="text-caption axo-muted mb-2">{{ FAULTS[fault]!.text }}</div>
          <template v-if="attempt.failure.traceback">
            <div class="d-flex align-center mb-1">
              <span class="text-caption font-weight-medium">Traceback</span>
              <v-spacer />
              <v-btn size="x-small" variant="text" prepend-icon="mdi-content-copy" class="text-none" @click="copy(attempt.failure.traceback!)">Copy</v-btn>
            </div>
            <AxoCode :model-value="attempt.failure.traceback" language="text" readonly min-height="60px" max-height="320px" label="Traceback" />
          </template>
          <div v-else-if="fault === 'code' && !canSeeTraceback" class="text-caption axo-muted">
            The traceback is shown to Developers and above.
          </div>
        </template>

        <div v-if="reads.length || writes.length" class="mt-3">
          <div class="text-caption font-weight-medium mb-1">Data</div>
          <div v-for="r in reads" :key="`r${r[0]}`" class="text-body-2">Read {{ formatBytes(r[1]) }} from <span class="axo-mono">{{ r[0] }}</span></div>
          <div v-for="w in writes" :key="`w${w[0]}`" class="text-body-2">Wrote {{ formatBytes(w[1]) }} to <span class="axo-mono">{{ w[0] }}</span></div>
          <div v-if="(attempt.cache_hits ?? 0) + (attempt.cache_misses ?? 0) > 0" class="text-caption axo-muted">
            Chunk cache: {{ attempt.cache_hits ?? 0 }} found, {{ attempt.cache_misses ?? 0 }} downloaded
          </div>
        </div>
        <div v-if="attempt.retry_at" class="text-caption axo-muted mt-2">Next attempt at {{ formatExactTime(ts(attempt.retry_at)) }}</div>
        <div v-if="!attempt.failure && !reads.length && !writes.length" class="text-body-2 axo-muted">Nothing else recorded for this attempt.</div>
      </div>
    </v-expand-transition>
  </v-card>
</template>

<script setup lang="ts">
import type { JobAttempt } from '../utils/jobs'

// One attempt of a job: how it ended and why it started, where its time
// went, and (opened) its error with the traceback (Developers and above),
// the data it read / wrote. `cost`: Owners / Admins only.
const props = defineProps<{ attempt: JobAttempt; cost?: number | null; startOpen?: boolean; canSeeTraceback: boolean }>()
const opened = ref(!!props.startOpen)
const fault = computed(() => faultOf(props.attempt.outcome))
const tone = computed(() => ({ completed: 'success', failed_user: 'error', failed_platform: 'warning',
  timed_out_in_queue: 'warning', cancelled: 'muted' } as Record<string, string>)[props.attempt.outcome] ?? 'muted')
const icon = computed(() => ({ completed: 'mdi-check-circle', failed_user: 'mdi-bug', failed_platform: 'mdi-server-off',
  timed_out_in_queue: 'mdi-timer-sand-complete', cancelled: 'mdi-cancel' } as Record<string, string>)[props.attempt.outcome] ?? 'mdi-circle-outline')
const outcomeText = computed(() => ({ completed: 'Succeeded', failed_user: 'Failed', failed_platform: 'Failed',
  timed_out_in_queue: 'Never ran', cancelled: props.attempt.failure?.message ? props.attempt.failure.message.replace(/^c/, 'C') : 'Cancelled' } as Record<string, string>)[props.attempt.outcome] ?? props.attempt.outcome)
const reasonText = computed(() => {
  const base = ATTEMPT_REASONS[props.attempt.reason ?? 'first'] ?? props.attempt.reason
  return props.attempt.reason === 'manual' && props.attempt.retried_by ? `${base} (${props.attempt.retried_by})` : base
})
const reads = computed(() => Object.entries(props.attempt.bytes_read ?? {}))
const writes = computed(() => Object.entries(props.attempt.bytes_written ?? {}))
async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); useNotifier().success('Copied') } catch { useNotifier().warning("Couldn't copy") }
}
</script>

<style scoped>
.axo-attempt { border-left-width: 4px !important; }
.axo-attempt--success { border-left-color: rgb(var(--v-theme-success)) !important; }
.axo-attempt--error { border-left-color: rgb(var(--v-theme-error)) !important; }
.axo-attempt--warning { border-left-color: rgb(var(--v-theme-warning)) !important; }
</style>
