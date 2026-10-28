<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 pt-0 mb-1" />
    <div class="d-flex align-center flex-wrap ga-2 mb-4">
      <div class="text-h6">{{ base ? `New version of ${base.name}` : 'New function' }}</div>
      <v-spacer />
      <span v-if="draftSavedAt" class="text-caption axo-muted">Draft kept in this browser · {{ timeAgo(draftSavedAt) }}</span>
      <v-btn v-if="draftSavedAt" size="small" variant="text" class="text-none" @click="discardDraft">Discard the draft</v-btn>
    </div>

    <v-alert v-if="!can('developer')" type="info" variant="tonal" density="compact" class="mb-4">Developers and above register functions.</v-alert>
    <v-alert v-else-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: start it to register functions.
    </v-alert>

    <v-row v-if="can('developer')" density="compact">
      <v-col cols="12" lg="8">
        <v-card variant="outlined" class="pa-4 mb-4">
          <div class="d-flex align-center flex-wrap ga-2 mb-3">
            <v-text-field v-model="name" label="Function name" density="compact" variant="outlined" class="axo-name axo-mono"
                          :disabled="!!base" :error-messages="nameError" hide-details="auto"
                          hint="The def in the code with this name runs." persistent-hint />
            <v-spacer />
            <v-btn size="small" variant="tonal" class="text-none" prepend-icon="mdi-upload" @click="fileInput?.click()">Upload a .py</v-btn>
            <input ref="fileInput" type="file" accept=".py,text/x-python" hidden @change="upload">
          </div>

          <AxoCode ref="editor" v-model="source" language="python" min-height="320px" max-height="65vh" label="Code"
                   :problems="check?.problems ?? []" @run="register" />

          <div class="d-flex align-center flex-wrap ga-2 mt-2 text-body-2">
            <template v-if="checking"><v-progress-circular indeterminate size="16" width="2" /> Checking…</template>
            <template v-else-if="check?.ok">
              <v-icon color="success" size="18">mdi-check-circle</v-icon>
              <span>Looks right: {{ check.params_schema.length }} parameter{{ check.params_schema.length === 1 ? '' : 's' }}</span>
              <span v-if="check.existing_version" class="text-warning">· the same code as v{{ check.existing_version }} (No changes)</span>
            </template>
            <template v-else-if="check">
              <v-icon color="error" size="18">mdi-alert-circle</v-icon>
              <span>{{ check.problems.length }} problem{{ check.problems.length === 1 ? '' : 's' }}:</span>
              <a v-for="p in check.problems.slice(0, 3)" :key="`${p.line}:${p.message}`" href="#" class="axo-link"
                 @click.prevent="editor?.goTo(p.line)">line {{ p.line }} — {{ p.message }}</a>
            </template>
            <AxoErrorAlert v-if="checkError" :error="checkError" title="The check couldn't run" density="compact" />
            <v-spacer />
            <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-check" :loading="checking" @click="runCheck">Check</v-btn>
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" lg="4">
        <AxoPanel title="How it runs" icon="mdi-cog-outline" stacked class="mb-3">
          <v-radio-group v-model="kind" hide-details density="compact" class="mb-2">
            <v-radio value="process">
              <template #label><div><div class="text-body-2">Simple</div>
                <div class="text-caption axo-muted">On the endpoints, with the platform's Python and packages. Starts at once.</div></div></template>
            </v-radio>
            <v-radio value="container" class="mt-2">
              <template #label><div><div class="text-body-2">Container</div>
                <div class="text-caption axo-muted">Its own Python and packages. Built on its first job (that one waits for the build).</div></div></template>
            </v-radio>
          </v-radio-group>
          <template v-if="kind === 'container'">
            <v-select v-model="python" :items="PYTHON_VERSIONS" label="Python" density="compact" variant="outlined" class="mt-3" hide-details />
            <v-textarea v-model="requirements" label="Packages (one per line)" density="compact" variant="outlined" rows="3" auto-grow
                        class="mt-3 axo-mono" placeholder="pillow==12.0.0" hide-details />
            <RunnerSizeFields v-model:cpu="runnerCpu" v-model:memory-mb="runnerMb" class="mt-3" />
          </template>
        </AxoPanel>

        <AxoPanel title="Attempts and results" icon="mdi-refresh" stacked class="mb-3">
          <v-text-field v-model.number="maxAttempts" type="number" min="1" max="10" label="Attempts in all" density="compact" variant="outlined"
                        :error-messages="attemptsError" hint="When the code fails." persistent-hint />
          <div v-if="maxAttempts > 1" class="d-flex ga-2 mt-3">
            <v-text-field v-model.number="retryDelay" type="number" min="0" label="Wait (s)" density="compact" variant="outlined" hide-details />
            <v-select v-model="backoff" :items="BACKOFFS" label="Each wait" density="compact" variant="outlined" hide-details />
          </div>
          <v-select v-model="keep" :items="keepChoices" label="Keep results for" density="compact" variant="outlined" class="mt-3" hide-details />
        </AxoPanel>

        <AxoPanel title="Environment variables" icon="mdi-variable" stacked class="mb-3"
                  help="Given to the code as environment variables. A secret's value is never shown again.">
          <div v-for="(e, i) in env" :key="i" class="mb-2">
            <div class="d-flex ga-2">
              <v-text-field v-model="e.name" label="Name" density="compact" variant="outlined" hide-details class="axo-mono" />
              <v-btn icon="mdi-close" size="small" variant="text" :aria-label="`Remove ${e.name || 'variable'}`" @click="env.splice(i, 1)" />
            </div>
            <div class="d-flex align-center ga-2 mt-1">
              <v-text-field v-model="e.value" label="Value" :type="e.secret ? 'password' : 'text'" density="compact" variant="outlined"
                            hide-details class="axo-mono" />
              <v-checkbox v-model="e.secret" label="Secret" density="compact" hide-details class="flex-grow-0" />
            </div>
          </div>
          <div v-if="envError" class="text-caption text-error mb-2">{{ envError }}</div>
          <v-btn size="small" variant="tonal" class="text-none" prepend-icon="mdi-plus" @click="env.push({ name: '', value: '', secret: false })">Add a variable</v-btn>
        </AxoPanel>

        <AxoErrorAlert v-if="registerError" :error="registerError" title="It couldn't be registered" class="mb-3" />
        <AxoBtn block size="large" prepend-icon="mdi-check" :loading="registering" :disabled="!ready" @click="register">
          {{ base ? `Save as v${nextVersion}` : 'Register' }}
        </AxoBtn>
        <div class="text-caption axo-muted mt-2 text-center">Ctrl + Enter in the editor</div>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import type { ParamSpec } from '~/utils/functions'

// Registers a function from the browser: write it (or upload a .py), it's
// checked as you type (never run), choose how it runs, its environment
// variables, attempts and result keep time. `?function=<id>` starts from
// that function's newest code and saves it as its next version ("No
// changes" when the code is the same). A draft is kept in this browser.
interface CheckAnswer {
  ok: boolean; problems: Array<{ line: number; column?: number; message: string }>
  params_schema: ParamSpec[]; description: string | null; functions: string[]; code_hash: string; existing_version?: number | null
}
const { id, ve, can, vemUp, api } = useCurrentVe()
const functions = useFunctions()
const route = useRoute()
const router = useRouter()
const notifier = useNotifier()
useHead(() => ({ title: `New function · ${ve.value?.name ?? 'VE'} · axo` }))

const baseId = computed(() => (route.query.function as string) || null)
const base = computed(() => (baseId.value ? functions.byId(baseId.value) : undefined))
const nextVersion = computed(() => Math.max(0, ...(base.value?.versions.map(v => v.version) ?? [0])) + 1)
const crumbs = computed(() => [{ title: 'Functions', to: `/ves/${id.value}/functions` },
  ...(base.value ? [{ title: base.value.name, to: `/ves/${id.value}/functions/${base.value.function_id}` }] : []),
  { title: base.value ? 'New version' : 'New', disabled: true }])

const editor = ref<{ goTo: (line: number) => void } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const name = ref('resize')
const source = ref(STARTER_CODE)
const kind = ref<'process' | 'container'>('process')
const python = ref('3.12')
const requirements = ref('')
const runnerCpu = ref<number | null>(null)       // null = the plan's default
const runnerMb = ref<number | null>(null)
const maxAttempts = ref(1)
const retryDelay = ref(5)
const backoff = ref<'fixed' | 'exponential'>('fixed')
const keep = ref<string | null>(null)
const env = ref<Array<{ name: string; value: string; secret: boolean }>>([])
const BACKOFFS = [{ value: 'fixed', title: 'The same' }, { value: 'exponential', title: 'Twice as long' }]

const veSettings = ref<{ result_keep_max?: string; result_keep_default?: string } | null>(null)
const keepChoices = computed(() => [{ value: null, title: `The VE’s default (${keepTitle(veSettings.value?.result_keep_default ?? '7d')})` },
  ...keepUpTo(veSettings.value?.result_keep_max)])

const nameError = computed(() => (!name.value ? 'A name' : isIdentifier(name.value) ? undefined : 'A Python name: letters, digits and _'))
const attemptsError = computed(() => (maxAttempts.value < 1 || maxAttempts.value > 10 ? 'From 1 to 10' : undefined))
const envError = computed(() => {
  const names = env.value.map(e => e.name)
  if (names.some(n => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(n))) return 'Names: letters, digits and _'
  if (new Set(names).size !== names.length) return 'A name is used twice'
  return null
})

// ── the check (as you type, and the Check button) ─────────────────────────
const check = ref<CheckAnswer | null>(null)
const checking = ref(false)
const checkError = ref<unknown>(null)
let checkTimer: ReturnType<typeof setTimeout> | null = null
let checkGeneration = 0
async function runCheck() {
  if (!vemUp.value || !name.value) return
  const mine = ++checkGeneration
  checking.value = true
  try {
    const answer = await api<CheckAnswer>('/functions/check', { method: 'POST',
      body: { name: name.value, source: source.value, function_id: baseId.value ?? undefined } })
    if (mine !== checkGeneration) return
    check.value = answer
    checkError.value = null
  } catch (err) {
    if (mine === checkGeneration) checkError.value = err
  } finally {
    if (mine === checkGeneration) checking.value = false
  }
}
watch([source, name], () => {
  if (checkTimer) clearTimeout(checkTimer)
  checkTimer = setTimeout(runCheck, 700)
  saveDraft()
})
watch([kind, python, requirements, runnerCpu, runnerMb, maxAttempts, retryDelay, backoff, keep, env], saveDraft, { deep: true })

const ready = computed(() => vemUp.value && !nameError.value && !attemptsError.value && !envError.value && !!check.value?.ok
  && !checking.value && !registering.value)

// ── drafts (this browser) ─────────────────────────────────────────────────
const draftKey = computed(() => `axo-function-draft:${id.value}:${baseId.value ?? 'new'}`)
const draftSavedAt = ref<number | null>(null)
let restoring = true
function saveDraft() {
  if (restoring || !import.meta.client) return
  // secrets never go into the browser's storage
  const draft = { name: name.value, source: source.value, kind: kind.value, python: python.value, requirements: requirements.value,
    runnerCpu: runnerCpu.value, runnerMb: runnerMb.value, maxAttempts: maxAttempts.value, retryDelay: retryDelay.value, backoff: backoff.value, keep: keep.value,
    env: env.value.map(e => ({ ...e, value: e.secret ? '' : e.value })) }
  try {
    localStorage.setItem(draftKey.value, JSON.stringify({ at: Date.now() / 1000, draft }))
    draftSavedAt.value = Date.now() / 1000
  } catch { /* private window */ }
}
function discardDraft() {
  try { localStorage.removeItem(draftKey.value) } catch { /* nothing kept */ }
  draftSavedAt.value = null
  restoring = true
  fillFrom(null)
  nextTick(() => { restoring = false })
}
function readDraft(): any {
  try { return JSON.parse(localStorage.getItem(draftKey.value) ?? 'null') } catch { return null }
}

async function fillFrom(draft: any) {
  if (draft) {
    name.value = base.value?.name ?? draft.name
    source.value = draft.source; kind.value = draft.kind; python.value = draft.python; requirements.value = draft.requirements
    runnerCpu.value = draft.runnerCpu ?? null; runnerMb.value = draft.runnerMb ?? null
    maxAttempts.value = draft.maxAttempts; retryDelay.value = draft.retryDelay; backoff.value = draft.backoff; keep.value = draft.keep
    env.value = draft.env ?? []
    return
  }
  if (base.value) {
    const v = base.value.latest
    const s = v.runtime_spec ?? { type: 'process' }
    name.value = base.value.name
    kind.value = s.type === 'container' ? 'container' : 'process'
    python.value = s.python_version ?? '3.12'
    requirements.value = (s.requirements ?? []).join('\n')
    runnerCpu.value = s.cpu_limit ?? null
    runnerMb.value = s.memory_limit_bytes ? Math.round(s.memory_limit_bytes / 1024 / 1024) : null
    maxAttempts.value = s.max_attempts ?? 1
    retryDelay.value = s.retry_delay_seconds ?? 5
    backoff.value = (s.retry_backoff as any) ?? 'fixed'
    keep.value = s.keep_result ?? null
    env.value = Object.entries(s.env_vars ?? {}).map(([n, val]) => ({ name: n, value: val ?? '', secret: (s.secret_env ?? []).includes(n) }))
    try {
      const code = await api<{ text: string | null }>(`/functions/${base.value.function_id}/${v.version}/code`)
      source.value = code.text ?? ''
    } catch { source.value = '' }
    return
  }
  name.value = 'resize'
  source.value = STARTER_CODE
  kind.value = 'process'; python.value = '3.12'; requirements.value = ''; runnerCpu.value = null; runnerMb.value = null
  maxAttempts.value = 1; retryDelay.value = 5; backoff.value = 'fixed'; keep.value = null; env.value = []
}

onMounted(async () => {
  try { veSettings.value = (await api<{ settings: any }>('/ve')).settings } catch { /* defaults */ }
  if (baseId.value) await functions.load()
  const saved = readDraft()
  await fillFrom(saved?.draft ?? null)
  draftSavedAt.value = saved?.at ?? null
  await nextTick()
  restoring = false
  runCheck()
})

async function upload(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 1_000_000) { notifier.warning('That file is over 1 MB'); return }
  source.value = await file.text()
  // the name: the first def, if the current one isn't in the file
  const defs = [...source.value.matchAll(/^def\s+([A-Za-z_]\w*)\s*\(/gm)].map(m => m[1]!)
  if (!base.value && defs.length && !defs.includes(name.value)) name.value = defs[0]!
  ;(e.target as HTMLInputElement).value = ''
}

// ── register / save as vN ─────────────────────────────────────────────────
const registering = ref(false)
const registerError = ref<unknown>(null)
async function register() {
  if (!ready.value) { if (!check.value?.ok) runCheck(); return }
  if (check.value?.existing_version) {
    notifier.info(`No changes: the code is the same as v${check.value.existing_version}`)
    return
  }
  registering.value = true
  registerError.value = null
  const spec: Record<string, unknown> = { type: kind.value, max_attempts: maxAttempts.value }
  if (maxAttempts.value > 1) Object.assign(spec, { retry_delay_seconds: retryDelay.value, retry_backoff: backoff.value })
  if (keep.value) spec.keep_result = keep.value
  if (kind.value === 'container') {
    spec.python_version = python.value
    spec.requirements = requirements.value.split('\n').map(r => r.trim()).filter(Boolean)
    if (runnerCpu.value != null) spec.cpu_limit = runnerCpu.value
    if (runnerMb.value != null) spec.memory_limit_bytes = runnerMb.value * 1024 * 1024
  }
  if (env.value.length) {
    spec.env_vars = Object.fromEntries(env.value.map(e => [e.name, e.value]))
    spec.secret_env = env.value.filter(e => e.secret).map(e => e.name)
  }
  const form = new FormData()
  form.set('name', name.value)
  form.set('runtime_spec', JSON.stringify(spec))
  form.set('code_format', 'source')
  form.set('code_hash', check.value!.code_hash)
  form.set('call_style', 'kwargs')
  if (baseId.value) form.set('function_id', baseId.value)
  form.set('code', new Blob([source.value], { type: 'text/x-python' }), `${name.value}.py`)
  try {
    const answer = await api<{ function_id: string; version: number; existing?: boolean }>('/functions', { method: 'POST', body: form })
    try { localStorage.removeItem(draftKey.value) } catch { /* nothing kept */ }
    restoring = true
    await functions.load(true)
    if (answer.existing) notifier.info(`No changes: the code is the same as v${answer.version}`)
    else notifier.success(`${name.value} v${answer.version} registered`)
    router.push(`/ves/${id.value}/functions/${answer.function_id}`)
  } catch (err) {
    registerError.value = err
  } finally {
    registering.value = false
  }
}
</script>

<style scoped>
.axo-name { flex: 0 1 320px; }
</style>
