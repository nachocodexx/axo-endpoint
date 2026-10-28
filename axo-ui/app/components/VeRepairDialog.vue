<template>
  <v-dialog :model-value="modelValue" max-width="620" scrollable @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="d-flex align-center ga-2 pt-4">
        <v-icon color="primary">mdi-wrench-outline</v-icon> Repair {{ ve.name }}
      </v-card-title>
      <v-card-text>
        <template v-if="running">
          <div class="d-flex align-center ga-3 py-6">
            <v-progress-circular indeterminate color="primary" size="28" width="3" />
            <div>
              <div class="text-body-1">Checking every piece of the VE…</div>
              <div class="text-caption axo-muted">Network, Kurrent, Mongo, Redis, the VE manager, then the endpoints.
                It can take up to a minute when the VE manager has to be restarted.</div>
            </div>
          </div>
        </template>
        <AxoErrorAlert v-else-if="error" :error="error" title="The repair couldn't run" />
        <template v-else-if="report">
          <v-alert :type="summary.type" variant="tonal" density="compact" class="mb-4">{{ summary.text }}</v-alert>
          <v-list density="compact" class="pa-0">
            <v-list-item v-for="s in report.steps" :key="s.check" class="px-0">
              <template #prepend>
                <v-icon :color="STATUS[s.status]?.color" class="mr-3">{{ STATUS[s.status]?.icon }}</v-icon>
              </template>
              <v-list-item-title class="text-body-2 font-weight-medium">{{ s.name }}</v-list-item-title>
              <div class="text-body-2 axo-muted axo-step-text">{{ s.text }}</div>
            </v-list-item>
          </v-list>
          <div class="text-caption axo-muted mt-3">
            Recorded in the VE's activity. It can be repaired again {{ againText }}.
          </div>
        </template>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" class="text-none" :disabled="running" @click="emit('update:modelValue', false)">Close</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// Runs the Control Plane's Repair of one VE as soon as it opens and shows each
// check with what it did (ok / fixed / failed / later). A repair asked too
// soon after the last one shows when it can run again.
const props = defineProps<{ modelValue: boolean; ve: { ve_id: string; name: string } }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; repaired: [] }>()
const { cp } = useCurrentVe()

interface Step { check: string; name: string; status: 'ok' | 'fixed' | 'failed' | 'later'; text: string }
interface Report { outcome: 'ok' | 'fixed' | 'failed'; steps: Step[]; available_at: number }

const STATUS: Record<string, { icon: string; color: string }> = {
  ok: { icon: 'mdi-check-circle', color: 'success' },
  fixed: { icon: 'mdi-wrench-check', color: 'primary' },
  failed: { icon: 'mdi-close-circle', color: 'error' },
  later: { icon: 'mdi-timer-sand', color: 'warning' },
}

const running = ref(false)
const report = ref<Report | null>(null)
const error = ref<unknown>(null)

watch(() => props.modelValue, async (open) => {
  if (!open) return
  running.value = true
  report.value = null
  error.value = null
  try {
    report.value = await cp<Report>('/repair', { method: 'POST' })
    emit('repaired')
  } catch (err) { error.value = err } finally { running.value = false }
})

const summary = computed(() => {
  const r = report.value!
  const fixed = r.steps.filter(s => s.status === 'fixed').length
  const later = r.steps.some(s => s.status === 'later')
  if (r.outcome === 'failed') return { type: 'error' as const, text: 'Some checks still fail: the platform keeps trying on its own. If it stays like this, contact your platform admin.' }
  if (later) return { type: 'warning' as const, text: 'Fixed what could be fixed; the VE manager is still starting, and failed endpoints are tried again as soon as it answers.' }
  if (fixed) return { type: 'success' as const, text: `${fixed} thing${fixed === 1 ? ' was' : 's were'} fixed. The VE should be back in a minute.` }
  return { type: 'info' as const, text: 'Everything was working: nothing had to change.' }
})
const againText = computed(() => (report.value ? `at ${formatTime(report.value.available_at)}` : 'in 5 minutes'))
</script>

<style scoped>
.axo-step-text { white-space: normal; }
</style>
