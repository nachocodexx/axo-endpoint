<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="The settings couldn't load" :retry="load" class="mb-4" />
    <v-alert v-if="asleep" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: its settings can be read and changed once it runs (a sleeping VE wakes with its next job).
    </v-alert>
    <AxoSkeleton v-if="!form && !error && !asleep" :lines="10" />

    <v-form v-if="form" ref="formRef" v-model="valid" :disabled="!can('admin')" @submit.prevent="save">
      <v-alert v-if="!can('admin')" type="info" variant="tonal" density="compact" class="mb-4">
        Only Admins and the Owner change these.
      </v-alert>
      <v-row>
        <v-col cols="12" lg="6">
          <AxoPanel title="Endpoints" icon="mdi-server-outline" stacked>
            <div class="text-body-2 mb-1">Endpoints to run: <b>{{ form.target_endpoints }}</b>
              <span class="axo-muted">(plan maximum {{ limits.max_endpoints }})</span></div>
            <v-slider v-model="form.target_endpoints" :min="0" :max="limits.max_endpoints" :step="1" show-ticks="always" thumb-label hide-details class="mb-3" />
            <div class="text-body-2 mb-1 d-flex align-center ga-1">Always keep at least: <b>{{ form.min_endpoints }}</b>
              <AxoHelp title="Minimum endpoints" text="Scaling policies and idle sleeping never go below this; health counts as Degraded under it." /></div>
            <v-slider v-model="form.min_endpoints" :min="0" :max="form.target_endpoints" :step="1" show-ticks="always" thumb-label hide-details class="mb-3" />
            <v-switch v-model="form.auto_replace_failed" color="primary" inset hide-details label="Replace an endpoint that fails" class="mb-2" />
            <div class="text-body-2 mt-2 mb-1 d-flex align-center ga-1">Size of new endpoints
              <AxoHelp title="Size of new endpoints" text="Endpoints already running keep their size; new ones (to reach the target, or replacing a failed one) get this." /></div>
            <v-row density="compact">
              <v-col cols="6"><v-text-field v-model.number="form.new_endpoint_cpu_cores" type="number" step="0.5" min="0.25" label="CPU cores" /></v-col>
              <v-col cols="6"><v-text-field v-model.number="form.new_endpoint_memory_mb" type="number" step="256" min="256" label="Memory (MB)" /></v-col>
            </v-row>
            <div v-if="quotaProblem" class="text-caption text-error">{{ quotaProblem }}</div>
            <div v-else-if="limits.quota_cpu_cores" class="text-caption axo-muted">
              Quota {{ limits.quota_cpu_cores }} CPU · {{ formatMemoryMb(limits.quota_memory_mb) }}.
            </div>
          </AxoPanel>
        </v-col>

        <v-col cols="12" lg="6">
          <AxoPanel title="Idle and waking" icon="mdi-sleep" stacked>
            <v-select v-model="idleChoice" :items="IDLE" label="An idle endpoint sleeps after" class="mb-2"
                      hint="A sleeping endpoint costs nothing; the next job wakes it (that job waits for the start)." persistent-hint />
            <v-text-field v-if="idleChoice === 'custom'" v-model.number="form.endpoint_idle_seconds" type="number" min="30" label="Seconds" class="mb-2" />
            <v-text-field v-model.number="form.wake_wait_seconds" type="number" min="0" step="0.5" label="Wake another endpoint when a job waits (seconds)"
                          hint="With sleeping endpoints: a job waiting longer than this wakes one more." persistent-hint class="mt-4" />
          </AxoPanel>

          <AxoPanel title="Jobs and queues" icon="mdi-tray-full" stacked class="mt-4">
            <v-select v-model="waitChoice" :items="waitItems" label="A job waits for a free runner" class="mb-2"
                      hint="For jobs that don't say it themselves (their max_wait)." persistent-hint />
            <v-text-field v-if="waitChoice === 'custom'" v-model.number="form.default_max_wait_seconds" type="number" min="1"
                          :max="limits.max_wait_seconds ?? undefined" label="Seconds"
                          :rules="[v => limits.max_wait_seconds == null || v <= limits.max_wait_seconds || `At most ${limits.max_wait_seconds}`]" />
            <div class="text-body-2 mt-4 mb-1 d-flex align-center ga-1">Queue limits
              <AxoHelp title="Queue limits" :text="`How many jobs may wait at each layer before new ones are turned away. Empty = the platform default${limits.max_queue_limit ? ` (at most ${limits.max_queue_limit})` : ''}.`" /></div>
            <v-row density="compact">
              <v-col v-for="q in QUEUES" :key="q.key" cols="12" sm="4">
                <v-text-field v-model.number="form[q.key]" type="number" min="1" :label="q.title" clearable placeholder="Default"
                              :rules="[v => v == null || v === '' || limits.max_queue_limit == null || v <= limits.max_queue_limit || `At most ${limits.max_queue_limit}`]" />
              </v-col>
            </v-row>
          </AxoPanel>

          <AxoPanel title="Job results" icon="mdi-package-variant-closed" stacked class="mt-4"
                    help="Every job's result is kept in the VE's results bucket (billed as storage to the VE's owner) and deleted when its keep time ends; the job then shows when it was deleted.">
            <v-select v-model="form.result_keep_max" :items="KEEP_CHOICES" label="The longest a result may be kept" class="mb-2"
                      hint="A job or function can't ask for longer." persistent-hint />
            <v-select v-model="form.result_keep_default" :items="keepUpTo(form.result_keep_max)" label="Kept for, unless a job says otherwise"
                      class="mt-4" :error-messages="keepDefaultProblem" />
            <v-alert v-if="resultsShortened" type="warning" variant="tonal" density="compact" class="mt-2">
              {{ resultsShortened }} kept result{{ resultsShortened === 1 ? '' : 's' }} will now be deleted sooner (at most
              {{ keepTitle(form.result_keep_max).toLowerCase() }} after their job finished, so some go at once).
            </v-alert>
          </AxoPanel>

          <AxoPanel title="History" icon="mdi-history" stacked class="mt-4">
            <v-text-field v-model.number="form.retention_days" type="number" min="1" :max="limits.max_retention_days ?? undefined" clearable
                          label="Keep history for (days)" :placeholder="`Plan maximum (${limits.max_retention_days ?? '∞'})`"
                          :rules="[v => v == null || v === '' || limits.max_retention_days == null || v <= limits.max_retention_days || `The plan keeps at most ${limits.max_retention_days} days`]"
                          :hint="`Activity, jobs and choreography runs older than this are hidden at once and deleted for good ${graceDays} days later, so a mistake or a plan upgrade within ${graceDays} days loses nothing. Bills are never affected.`"
                          persistent-hint />
            <v-alert v-if="shortens" type="warning" variant="tonal" density="compact" class="mt-4">
              History older than {{ keptAfter }} days will be hidden when you save, and deleted for good after {{ graceDays }} more days.
            </v-alert>
          </AxoPanel>
        </v-col>
      </v-row>

      <div v-if="can('admin')" class="d-flex align-center flex-wrap ga-3 mt-6">
        <v-text-field v-model="reason" label="Why (optional, kept in the history)" density="compact" hide-details style="max-width: 420px" />
        <v-spacer />
        <span v-if="dirty" class="text-body-2 axo-muted">{{ Object.keys(changes).length }} change{{ Object.keys(changes).length === 1 ? '' : 's' }}</span>
        <AxoBtn look="plain" variant="text" :disabled="!dirty" @click="reset">Undo</AxoBtn>
        <AxoBtn type="submit" :loading="saving" :disabled="!dirty || !valid || !!quotaProblem || !!keepDefaultProblem" prepend-icon="mdi-content-save-outline">Save</AxoBtn>
      </div>
    </v-form>

    <AxoPanel v-if="can('owner')" title="Transfer ownership" icon="mdi-account-switch-outline" stacked class="mt-6"
              help="Give the VE to another person or team. They must accept; from then on they own it and pay its renewals. Everything in it keeps running.">
      <div class="d-flex align-center flex-wrap ga-3">
        <div class="text-body-2 axo-muted">Today it belongs to {{ ve?.owner.kind === 'team' ? `the team ${ve?.owner.name}` : 'you' }}.</div>
        <v-spacer />
        <AxoBtn look="plain" prepend-icon="mdi-account-switch-outline" @click="transferOpen = true">Transfer…</AxoBtn>
      </div>
    </AxoPanel>
    <TransferDialog v-if="ve" v-model="transferOpen" kind="ve" :target="id" :target-name="ve.name"
                    :current-owner="ve.owner.kind === 'team' ? ve.owner.name ?? undefined : undefined" />
  </div>
</template>

<script setup lang="ts">
// The VE's settings (the VEM's GET /ve: settings + plan limits), checked
// here against the same limits; PATCH /ve/settings sends only what changed.
// A manual target change also pauses the scaling policies for a while.
const { id, ve, can, vemUp, api } = useCurrentVe()
const notify = useNotifier()
const transferOpen = ref(false)

const IDLE = [
  { title: 'Never', value: 'never' }, { title: '5 minutes', value: 300 }, { title: '15 minutes', value: 900 },
  { title: '30 minutes', value: 1800 }, { title: '1 hour', value: 3600 }, { title: 'Custom…', value: 'custom' },
]
const QUEUES = [
  { key: 'vem_queue_limit', title: 'Waiting for an endpoint' },
  { key: 'endpoint_queue_limit', title: 'In an endpoint' },
  { key: 'runner_queue_limit', title: 'For a runner' },
] as const

const saved = ref<Record<string, any> | null>(null)
const form = ref<Record<string, any> | null>(null)
const limits = ref<Record<string, any>>({})
const graceDays = computed(() => limits.value.history_grace_days ?? 7)
// Days kept now and after the change (empty = the plan maximum).
const keptNow = computed(() => saved.value?.retention_days ?? limits.value.max_retention_days ?? null)
const keptAfter = computed(() => (form.value?.retention_days || null) ?? limits.value.max_retention_days ?? null)
const shortens = computed(() => keptNow.value != null && keptAfter.value != null && keptAfter.value < keptNow.value)
const error = ref<unknown>(null)
const valid = ref(true)
const reason = ref('')
const asleep = computed(() => !vemUp.value)

async function load() {
  if (asleep.value) return
  error.value = null
  try { apply(await api('/ve')) } catch (err) { error.value = err }
}
function apply(view: any) {
  limits.value = view.plan_limits
  saved.value = { ...view.settings }
  reset()
}
function reset() {
  form.value = { ...saved.value }
  idleChoice.value = idleOf(form.value!.endpoint_idle_seconds)
  waitChoice.value = waitOf(form.value!.default_max_wait_seconds)
  reason.value = ''
}
watch(id, load)
watch(asleep, (now) => { if (!now && !form.value) load() })
onMounted(load)

// ── choices that map onto one number ───────────────────────────────────────
const idleOf = (s: number | null) => (s == null ? 'never' : IDLE.some(i => i.value === s) ? s : 'custom')
const idleChoice = ref<any>('never')
watch(idleChoice, (c) => {
  if (!form.value) return
  if (c === 'never') form.value.endpoint_idle_seconds = null
  else if (c !== 'custom') form.value.endpoint_idle_seconds = c
  else if (form.value.endpoint_idle_seconds == null) form.value.endpoint_idle_seconds = 600
})
const waitItems = computed(() => [
  ...(limits.value.allow_wait_forever !== false ? [{ title: 'As long as it takes', value: -1 }] : []),
  { title: "Not at all (fail if it would queue)", value: 0 },
  { title: 'Up to…', value: 'custom' },
])
const waitOf = (s: number) => (s === -1 || s === 0 ? s : 'custom')
const waitChoice = ref<any>(-1)
watch(waitChoice, (c) => {
  if (!form.value) return
  if (c !== 'custom') form.value.default_max_wait_seconds = c
  else if (form.value.default_max_wait_seconds <= 0) form.value.default_max_wait_seconds = 30
})

const quotaProblem = computed(() => {
  const f = form.value
  const l = limits.value
  if (!f || l.quota_cpu_cores == null) return null
  if (f.target_endpoints * f.new_endpoint_cpu_cores > l.quota_cpu_cores
    || f.target_endpoints * f.new_endpoint_memory_mb > l.quota_memory_mb) {
    return `${f.target_endpoints} × (${f.new_endpoint_cpu_cores} CPU · ${formatMemoryMb(f.new_endpoint_memory_mb)}) doesn't fit the quota of ${l.quota_cpu_cores} CPU · ${formatMemoryMb(l.quota_memory_mb)}.`
  }
  return null
})

// ── job results: the longest keep time and the default ─────────────────────
const keepDefaultProblem = computed(() => {
  const f = form.value
  if (!f?.result_keep_default || !f.result_keep_max) return undefined
  return keepUpTo(f.result_keep_max).some(k => k.value === f.result_keep_default) ? undefined
    : `Longer than the longest (${keepTitle(f.result_keep_max).toLowerCase()})`
})
// A shorter longest time: how many kept results it would cut short (asked to the VEM).
const resultsShortened = ref(0)
watch(() => form.value?.result_keep_max, async (longest) => {
  resultsShortened.value = 0
  if (!longest || longest === saved.value?.result_keep_max || longest === 'never') return
  try {
    const answer = await api<{ shortened: number }>(`/ve/results/shortened?longest=${longest}`)
    if (form.value?.result_keep_max === longest) resultsShortened.value = answer.shortened
  } catch { /* no warning */ }
})

// ── saving ─────────────────────────────────────────────────────────────────
const blankToNull = (v: unknown) => (v === '' || v === undefined ? null : v)
const changes = computed(() => {
  if (!form.value || !saved.value) return {}
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(form.value)) if (blankToNull(v) !== saved.value[k]) out[k] = blankToNull(v)
  return out
})
const dirty = computed(() => Object.keys(changes.value).length > 0)
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    apply(await api('/ve/settings', { method: 'PATCH', body: { ...changes.value, reason: reason.value || undefined } }))
    notify.success('Settings saved')
  } catch (err) {
    notify.error(err, "The settings couldn't be saved")
  } finally {
    saving.value = false
  }
}
</script>
