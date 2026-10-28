<template>
  <div>
    <v-alert v-if="!can('developer')" type="info" variant="tonal" density="compact" class="mb-4">
      Viewers see the form; Developers and above run functions.
    </v-alert>
    <v-alert v-if="selected?.deleted_at" type="warning" variant="tonal" density="compact" class="mb-4">
      Version {{ selected.version }} was deleted: pick another one in Run options.
    </v-alert>

    <v-row density="compact">
      <v-col cols="12" lg="7">
        <v-card variant="outlined" class="pa-4 mb-4">
          <div class="d-flex align-center flex-wrap ga-2 mb-3">
            <span class="text-subtitle-1 font-weight-medium">Parameters</span>
            <span class="text-caption axo-muted">v{{ version }}</span>
            <v-spacer />
            <v-btn-toggle v-if="schema.length" v-model="mode" density="compact" variant="outlined" mandatory divided color="primary">
              <v-btn value="fields" class="text-none" size="small">Fields</v-btn>
              <v-btn value="json" class="text-none" size="small">Edit as JSON</v-btn>
            </v-btn-toggle>
          </div>

          <template v-if="mode === 'fields' && schema.length">
            <ParamField v-for="p in schema" :key="p.name" v-model="values[p.name]" :spec="p" :error="fieldErrors[p.name]"
                        @invalid="m => setInvalid(p.name, m)" @run="run" />
          </template>
          <template v-else>
            <AxoCode v-model="jsonText" language="json" min-height="140px" max-height="420px" label="Parameters as JSON"
                     :problems="jsonProblem ? [{ line: jsonProblemLine, message: jsonProblem }] : []" @run="run" />
            <div class="text-caption mt-1" :class="jsonProblem ? 'text-error' : 'axo-muted'">
              {{ jsonProblem || (schema.length ? 'Every parameter by name, as one JSON object.' : 'This function has no fields to show: its parameters by name, as one JSON object.') }}
            </div>
          </template>

          <v-expansion-panels v-model="optionsOpen" variant="accordion" class="mt-5" flat>
            <v-expansion-panel value="options" class="axo-options">
              <v-expansion-panel-title class="px-0 text-body-2">
                <v-icon size="18" class="mr-2">mdi-cog-outline</v-icon>Run options
                <span class="text-caption axo-muted ml-2">{{ optionsSummary }}</span>
              </v-expansion-panel-title>
              <v-expansion-panel-text class="axo-options-text">
                <v-row density="compact">
                  <v-col cols="12" sm="6">
                    <v-select v-model="version" :items="versionChoices" label="Version" density="compact" variant="outlined" hide-details />
                  </v-col>
                  <v-col cols="12" sm="6">
                    <v-select v-model="keep" :items="keepChoices" label="Keep the result for" density="compact" variant="outlined"
                              hide-details />
                  </v-col>
                  <v-col cols="12" sm="4">
                    <v-text-field v-model.number="maxAttempts" type="number" min="1" max="10" label="Attempts in all" density="compact"
                                  variant="outlined" :hint="`When the code fails. Default ${spec?.max_attempts ?? 1}.`" persistent-hint
                                  :error-messages="attemptsError" />
                  </v-col>
                  <v-col cols="12" sm="4">
                    <v-text-field v-model.number="retryDelay" type="number" min="0" label="Wait before trying again (s)" density="compact"
                                  variant="outlined" :disabled="(maxAttempts ?? 1) <= 1" hide-details />
                  </v-col>
                  <v-col cols="12" sm="4">
                    <v-select v-model="backoff" :items="BACKOFFS" label="Each wait" density="compact" variant="outlined"
                              :disabled="(maxAttempts ?? 1) <= 1" hide-details />
                  </v-col>
                  <v-col cols="12" sm="6">
                    <v-select v-model="maxWait" :items="WAITS" label="Wait for a free runner" density="compact" variant="outlined"
                              hint="If every runner is busy." persistent-hint />
                  </v-col>
                </v-row>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <AxoErrorAlert v-if="submitError" :error="submitError" title="The job couldn't start" class="mt-4" />

          <div class="d-flex align-center flex-wrap ga-3 mt-4">
            <AxoBtn prepend-icon="mdi-play" :loading="submitting" :disabled="!can('developer') || !!selected?.deleted_at || limitReached" @click="run">
              Run
            </AxoBtn>
            <span class="text-caption axo-muted">Ctrl + Enter</span>
            <v-spacer />
            <span v-if="ve?.requests" class="text-caption" :class="limitReached ? 'text-error' : 'axo-muted'">
              {{ ve.requests.used }} of {{ ve.requests.limit }} requests this period ({{ ve.plan_id }} plan)
            </span>
          </div>
        </v-card>

        <JobLive v-if="currentJob" :key="currentJob" :job-id="currentJob" class="mb-4" @finished="onFinished" />
      </v-col>

      <v-col cols="12" lg="5">
        <AxoPanel title="Your last runs" icon="mdi-history" stacked class="mb-3" flush>
          <v-list v-if="history.length" density="compact" class="bg-transparent py-0">
            <v-list-item v-for="h in history" :key="h.job_id">
              <v-list-item-title class="text-body-2">
                <NuxtLink :to="`/ves/${id}/jobs/${h.job_id}`" class="axo-link axo-mono">{{ h.job_id.slice(0, 12) }}</NuxtLink>
                <span class="text-caption axo-muted"> · v{{ h.version }} · {{ timeAgo(h.at) }}</span>
              </v-list-item-title>
              <v-list-item-subtitle class="axo-mono text-truncate">{{ JSON.stringify(h.params) }}</v-list-item-subtitle>
              <template #append>
                <v-btn size="small" variant="text" class="text-none" @click="useValues(h.params, h.version)">Use these values</v-btn>
              </template>
            </v-list-item>
          </v-list>
          <div v-else class="text-body-2 axo-muted pa-4">Runs you start here show up for reuse.</div>
        </AxoPanel>

        <AxoPanel title="Run from code" icon="mdi-language-python" stacked help="The same run with the axo SDK (pip install axo).">
          <template #actions>
            <v-btn size="small" variant="text" icon="mdi-content-copy" aria-label="Copy the code" @click="copySnippet" />
          </template>
          <AxoCode :model-value="snippet" language="python" readonly min-height="60px" max-height="260px" label="SDK code" />
        </AxoPanel>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import type { FunctionVersion } from '~/utils/functions'
import type { Job, JobDetail } from '~/utils/jobs'

// Runs a function: a field per parameter (from its type hints) or "Edit as
// JSON", Run options (version, result keep time, attempts, waiting), Run
// (Ctrl + Enter). The page stays: the job is followed live below, and the
// person's last 5 runs here can be reused. `from` (a job id) fills the form
// with that job's values (Run again).
const props = defineProps<{ versions: FunctionVersion[]; initialVersion?: number; fromJob?: string }>()
const { id, ve, can, api } = useCurrentVe()

const live = computed(() => props.versions.filter(v => !v.deleted_at))
// the function's default version unless the page asks for another
const defaultVersion = computed(() => live.value.find(v => v.is_default)?.version ?? live.value[0]?.version)
const version = ref<number>(props.initialVersion ?? defaultVersion.value ?? props.versions[0]?.version ?? 1)
const selected = computed(() => props.versions.find(v => v.version === version.value))
const spec = computed(() => selected.value?.runtime_spec)
const schema = computed(() => selected.value?.params_schema ?? [])
const versionChoices = computed(() => props.versions.map(v => ({ value: v.version,
  title: `v${v.version}${v.version === defaultVersion.value ? ' (default)' : ''}${v.version === live.value[0]?.version ? ' (newest)' : ''}${v.deleted_at ? ' (deleted)' : ''}`,
  props: { disabled: !!v.deleted_at } })))

// ── values ─────────────────────────────────────────────────────────────────
const mode = ref<'fields' | 'json'>('fields')
const values = reactive<Record<string, unknown>>({})
const invalid = reactive<Record<string, string>>({})
const fieldErrors = reactive<Record<string, string>>({})
const jsonText = ref('{}')
const jsonProblem = ref<string | null>(null)
const jsonProblemLine = ref(1)
const setInvalid = (name: string, m: string | null) => { if (m) invalid[name] = m; else delete invalid[name] }

function setValues(params: Record<string, unknown>) {
  for (const k of Object.keys(values)) delete values[k]
  Object.assign(values, params)
  jsonText.value = JSON.stringify(params, null, 2)
}
function current(): Record<string, unknown> {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v !== undefined))
}
// switching mode carries the values over
watch(mode, (m) => {
  if (m === 'json') jsonText.value = JSON.stringify(current(), null, 2)
  else {
    const parsed = parseJson()
    if (parsed) setValues(parsed)
  }
})
function parseJson(): Record<string, unknown> | null {
  try {
    const v = JSON.parse(jsonText.value || '{}')
    if (!v || typeof v !== 'object' || Array.isArray(v)) { jsonProblem.value = 'The parameters must be one JSON object: {"name": value, ...}'; return null }
    jsonProblem.value = null
    return v
  } catch (err) {
    const msg = (err as Error).message
    jsonProblem.value = `Not valid JSON: ${msg}`
    const pos = Number(/position (\d+)/.exec(msg)?.[1])
    jsonProblemLine.value = Number.isFinite(pos) ? jsonText.value.slice(0, pos).split('\n').length : 1
    return null
  }
}
watch(jsonText, () => { if (mode.value === 'json' || !schema.value.length) parseJson() })

/** Checks in the browser before sending (the server checks again). */
function check(params: Record<string, unknown>): boolean {
  for (const k of Object.keys(fieldErrors)) delete fieldErrors[k]
  let ok = true
  for (const p of schema.value) {
    const v = params[p.name]
    if (p.required && (v === undefined || v === '' || v === null)) { fieldErrors[p.name] = 'Required'; ok = false }
    else if (p.type === 'number' && v !== undefined && typeof v !== 'number') { fieldErrors[p.name] = 'A number'; ok = false }
    else if (invalid[p.name]) { fieldErrors[p.name] = invalid[p.name]!; ok = false }
  }
  if (!ok && mode.value === 'json') jsonProblem.value = `Check: ${Object.entries(fieldErrors).map(([k, m]) => `${k} (${m.toLowerCase()})`).join(', ')}`
  return ok
}

// ── run options ────────────────────────────────────────────────────────────
const optionsOpen = ref<string[]>([])
const veSettings = ref<{ result_keep_max?: string; result_keep_default?: string; default_max_wait_seconds?: number } | null>(null)
const keep = ref<string>('')
const maxAttempts = ref<number | null>(null)
const retryDelay = ref<number | null>(null)
const backoff = ref<'fixed' | 'exponential'>('fixed')
const maxWait = ref<number | null>(null)
const BACKOFFS = [{ value: 'fixed', title: 'The same' }, { value: 'exponential', title: 'Twice as long' }]
const WAITS = [{ value: null, title: 'The VE’s default' }, { value: -1, title: 'As long as needed' }, { value: 0, title: 'Not at all (fail at once)' },
  { value: 60, title: '1 minute' }, { value: 600, title: '10 minutes' }, { value: 3600, title: '1 hour' }]
const keepChoices = computed(() => [
  { value: '', title: `The default (${keepTitle(spec.value?.keep_result ?? veSettings.value?.result_keep_default ?? '7d')})` },
  ...keepUpTo(veSettings.value?.result_keep_max)])
const attemptsError = computed(() => (maxAttempts.value != null && (maxAttempts.value < 1 || maxAttempts.value > 10) ? 'From 1 to 10' : undefined))
watch(spec, (s) => {
  maxAttempts.value = s?.max_attempts ?? 1
  retryDelay.value = s?.retry_delay_seconds ?? 1
  backoff.value = (s?.retry_backoff as any) ?? 'fixed'
}, { immediate: true })
const optionsSummary = computed(() => [
  `v${version.value}`, keep.value ? `kept ${keepTitle(keep.value).toLowerCase()}` : null,
  (maxAttempts.value ?? 1) > 1 ? `${maxAttempts.value} attempts` : null,
].filter(Boolean).join(' · '))

onMounted(async () => {
  try { veSettings.value = (await api<{ settings: any }>('/ve')).settings } catch { /* defaults */ }
  if (props.fromJob) {
    try {
      const j = await api<JobDetail>(`/jobs/${props.fromJob}`)
      useValues(j.params ?? {}, j.function_version ?? undefined)
      useNotifier().info('The form has the values of that job')
    } catch { /* an empty form */ }
  }
})

function useValues(params: Record<string, unknown>, v?: number) {
  if (v && props.versions.some(x => x.version === v && !x.deleted_at)) version.value = v
  setValues(params)
}

// ── run ────────────────────────────────────────────────────────────────────
const submitting = ref(false)
const submitError = ref<unknown>(null)
const currentJob = ref<string | null>(null)
const limitReached = computed(() => !!ve.value?.requests && ve.value.requests.used >= ve.value.requests.limit)

async function run() {
  if (!can('developer') || submitting.value || selected.value?.deleted_at) return
  const params = mode.value === 'json' || !schema.value.length ? parseJson() : current()
  if (!params || !check(params) || attemptsError.value) return
  submitting.value = true
  submitError.value = null
  const body: Record<string, unknown> = { params }
  if (keep.value) body.keep_result = keep.value
  if (maxAttempts.value != null && maxAttempts.value !== (spec.value?.max_attempts ?? 1)) body.max_attempts = maxAttempts.value
  if ((maxAttempts.value ?? 1) > 1) { body.retry_delay_seconds = retryDelay.value ?? 1; body.retry_backoff = backoff.value }
  if (maxWait.value != null) body.max_wait = maxWait.value
  try {
    const answer = await api<{ job_id: string }>(`/functions/${selected.value!.function_id}/${version.value}/jobs`,
      { method: 'POST', body })
    currentJob.value = answer.job_id
    remember({ job_id: answer.job_id, at: Date.now() / 1000, params, version: version.value })
    if (ve.value?.requests) ve.value.requests.used += 1
  } catch (err) {
    submitError.value = err
    const field = (err as any)?.details?.field
    if (field && typeof field === 'string') fieldErrors[field] = (err as any).message
  } finally {
    submitting.value = false
  }
}
function onFinished(_: Job) { /* the job card shows the outcome */ }

// Ctrl / Cmd + Enter runs from anywhere on the page (not while a dialog is open).
function onKey(e: KeyboardEvent) {
  if (e.defaultPrevented || e.key !== 'Enter' || !(e.ctrlKey || e.metaKey) || document.querySelector('.v-overlay--active .v-dialog')) return
  e.preventDefault()
  run()
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// ── the person's last runs (this browser) ──────────────────────────────────
interface PastRun { job_id: string; at: number; params: Record<string, unknown>; version: number }
const storeKey = computed(() => `axo-runs:${id.value}:${props.versions[0]?.function_id}`)
const history = ref<PastRun[]>([])
onMounted(() => { try { history.value = JSON.parse(localStorage.getItem(storeKey.value) ?? '[]') } catch { history.value = [] } })
function remember(r: PastRun) {
  history.value = [r, ...history.value.filter(h => h.job_id !== r.job_id)].slice(0, 5)
  try { localStorage.setItem(storeKey.value, JSON.stringify(history.value)) } catch { /* private window */ }
}

const snippet = computed(() => sdkSnippet({ function_id: selected.value?.function_id ?? '', version: version.value,
  name: selected.value?.name ?? 'f' }, mode.value === 'json' ? (safeJson() ?? {}) : current(), ve.value?.name ?? id.value))
function safeJson() { try { return JSON.parse(jsonText.value || '{}') } catch { return null } }
async function copySnippet() {
  try { await navigator.clipboard.writeText(snippet.value); useNotifier().success('Copied') } catch { useNotifier().warning("Couldn't copy") }
}
</script>

<style scoped>
.axo-options { background: transparent !important; }
.axo-options :deep(.v-expansion-panel-text__wrapper) { padding: 8px 0 0; }
</style>
