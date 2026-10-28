<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 pt-0 mb-1" />
    <div class="d-flex align-center flex-wrap ga-2 mb-4">
      <div class="text-h6">{{ base ? `New version of ${base.class_name}` : 'New class' }}</div>
      <v-spacer />
      <span v-if="draftSavedAt" class="text-caption axo-muted">Draft kept in this browser · {{ timeAgo(draftSavedAt) }}</span>
      <v-btn v-if="draftSavedAt" size="small" variant="text" class="text-none" @click="discardDraft">Discard the draft</v-btn>
    </div>

    <v-alert v-if="!can('developer')" type="info" variant="tonal" density="compact" class="mb-4">Developers and above register classes.</v-alert>
    <v-alert v-else-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: start it to register classes.
    </v-alert>

    <v-row v-if="can('developer')" density="compact">
      <v-col cols="12" lg="8">
        <v-card variant="outlined" class="pa-4 mb-4">
          <div class="d-flex align-center flex-wrap ga-2 mb-3">
            <v-combobox v-model="className" :items="check?.classes ?? []" label="Class name" density="compact" variant="outlined"
                        class="axo-name axo-mono" :disabled="!!base" :error-messages="nameError" hide-details="auto"
                        hint="The class in the code with this name is registered." persistent-hint />
            <v-spacer />
            <v-btn size="small" variant="tonal" class="text-none" prepend-icon="mdi-upload" @click="fileInput?.click()">Upload a .py</v-btn>
            <input ref="fileInput" type="file" accept=".py,text/x-python" hidden @change="upload">
          </div>

          <AxoCode ref="editor" v-model="source" language="python" min-height="360px" max-height="65vh" label="Code"
                   :problems="(check?.problems ?? []).filter(p => p.line).map(p => ({ line: p.line!, message: p.message }))" @run="register" />

          <div class="d-flex align-center flex-wrap ga-2 mt-2 text-body-2">
            <template v-if="checking"><v-progress-circular indeterminate size="16" width="2" /> Checking…</template>
            <template v-else-if="check?.ok">
              <v-icon color="success" size="18">mdi-check-circle</v-icon>
              <span>Looks right: {{ check.methods.length }} method{{ check.methods.length === 1 ? '' : 's' }} people can call
                ({{ check.methods.map(m => m.name).join(', ') }}){{ check.has_upgrade ? ', with an upgrade step' : '' }}</span>
              <span v-if="check.existing_version" class="text-warning">· the same code as v{{ check.existing_version }} (No changes)</span>
            </template>
            <template v-else-if="check">
              <v-icon color="error" size="18">mdi-alert-circle</v-icon>
              <span>{{ check.problems.length }} problem{{ check.problems.length === 1 ? '' : 's' }}:</span>
              <template v-for="p in check.problems.slice(0, 3)" :key="`${p.line}:${p.message}`">
                <a v-if="p.line" href="#" class="axo-link" @click.prevent="editor?.goTo(p.line)">line {{ p.line }} — {{ p.message }}</a>
                <span v-else>{{ p.message }}</span>
              </template>
            </template>
            <AxoErrorAlert v-if="checkError" :error="checkError" title="The check couldn't run" density="compact" />
            <v-spacer />
            <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-check" :loading="checking" @click="runCheck">Check</v-btn>
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" lg="4">
        <AxoPanel title="What people can call" icon="mdi-function" stacked class="mb-3"
                  help="run and every method marked @axo_method (name= renames it). __init__ runs when an object is made; upgrade(self, old_version) runs when an object moves to this version.">
          <div v-if="!check?.methods.length" class="text-body-2 axo-muted">The methods show here as you write.</div>
          <div v-for="m in check?.methods ?? []" :key="m.name" class="text-body-2 axo-mono py-1">{{ m.name }}({{ m.params.join(', ') }})</div>
          <div v-if="check?.init" class="text-caption axo-muted mt-2 axo-mono">__init__({{ check.init.params.join(', ') }})</div>
        </AxoPanel>
        <AxoPanel title="How it runs" icon="mdi-cog-outline" stacked class="mb-3">
          <v-radio-group v-model="kind" hide-details density="compact" class="mb-2">
            <v-radio value="process">
              <template #label><div><div class="text-body-2">Simple</div>
                <div class="text-caption axo-muted">On the endpoints, with the platform's Python and packages.</div></div></template>
            </v-radio>
            <v-radio value="container" class="mt-2">
              <template #label><div><div class="text-body-2">Container</div>
                <div class="text-caption axo-muted">Its own Python and packages. Built on its first call.</div></div></template>
            </v-radio>
          </v-radio-group>
          <template v-if="kind === 'container'">
            <v-select v-model="python" :items="PYTHON_VERSIONS" label="Python" density="compact" variant="outlined" class="mt-3" hide-details />
            <v-textarea v-model="requirements" label="Packages (one per line)" density="compact" variant="outlined" rows="3" auto-grow
                        class="mt-3 axo-mono" placeholder="numpy==2.2.4" hide-details />
          </template>
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
import type { MethodInfo } from '~/utils/objects'

// Registers an Axo class from the browser: write it (or upload a .py), it's
// checked as you type (never run) — its methods, __init__ and upgrade step
// show — and how it runs. `?class=<name>` starts from that class's newest
// code and saves its next version ("No changes" when the code is the same).
// A draft is kept in this browser.
interface CheckAnswer {
  ok: boolean; problems: Array<{ line: number | null; column?: number | null; message: string }>
  classes: string[]; methods: MethodInfo[]; init: MethodInfo | null; has_upgrade: boolean
  description: string | null; code_hash: string; existing_version?: number | null
}
const { id, ve, can, vemUp, api } = useCurrentVe()
const store = useObjects()
const route = useRoute()
const router = useRouter()
const notifier = useNotifier()
useHead(() => ({ title: `New class · ${ve.value?.name ?? 'VE'} · axo` }))

const baseName = computed(() => (route.query.class as string) || null)
const base = computed(() => (baseName.value ? store.classOf(baseName.value) : undefined))
const nextVersion = computed(() => (base.value?.latest.version ?? 0) + 1)
const crumbs = computed(() => [{ title: 'Objects', to: `/ves/${id.value}/objects?tab=classes` },
  ...(base.value ? [{ title: base.value.class_name, to: `/ves/${id.value}/objects/classes/${encodeURIComponent(base.value.class_name)}` }] : []),
  { title: base.value ? 'New version' : 'New class', disabled: true }])

const editor = ref<{ goTo: (line: number) => void } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const className = ref('ShoppingCart')
const source = ref(CLASS_STARTER)
const kind = ref<'process' | 'container'>('process')
const python = ref('3.12')
const requirements = ref('')
const nameError = computed(() => (!className.value ? 'A name' : isIdentifier(className.value) ? undefined : 'A Python name: letters, digits and _'))

// ── the check ──────────────────────────────────────────────────────────────
const check = ref<CheckAnswer | null>(null)
const checking = ref(false)
const checkError = ref<unknown>(null)
let checkTimer: ReturnType<typeof setTimeout> | null = null
let checkGeneration = 0
async function runCheck() {
  if (!vemUp.value || !className.value) return
  const mine = ++checkGeneration
  checking.value = true
  try {
    const answer = await api<CheckAnswer>('/object-classes/check', { method: 'POST', body: { class_name: className.value, source: source.value } })
    if (mine !== checkGeneration) return
    check.value = answer
    checkError.value = null
  } catch (err) {
    if (mine === checkGeneration) checkError.value = err
  } finally {
    if (mine === checkGeneration) checking.value = false
  }
}
watch([source, className], () => {
  if (checkTimer) clearTimeout(checkTimer)
  checkTimer = setTimeout(runCheck, 700)
  saveDraft()
})
watch([kind, python, requirements], saveDraft)
const ready = computed(() => vemUp.value && !nameError.value && !!check.value?.ok && !checking.value && !registering.value)

// ── drafts ─────────────────────────────────────────────────────────────────
const draftKey = computed(() => `axo-class-draft:${id.value}:${baseName.value ?? 'new'}`)
const draftSavedAt = ref<number | null>(null)
let restoring = true
function saveDraft() {
  if (restoring || !import.meta.client) return
  const draft = { className: className.value, source: source.value, kind: kind.value, python: python.value, requirements: requirements.value }
  try {
    localStorage.setItem(draftKey.value, JSON.stringify({ at: Date.now() / 1000, draft }))
    draftSavedAt.value = Date.now() / 1000
  } catch { /* private window */ }
}
function readDraft(): any {
  try { return JSON.parse(localStorage.getItem(draftKey.value) ?? 'null') } catch { return null }
}
function discardDraft() {
  try { localStorage.removeItem(draftKey.value) } catch { /* nothing kept */ }
  draftSavedAt.value = null
  restoring = true
  fillFrom(null).then(() => nextTick(() => { restoring = false }))
}
async function fillFrom(draft: any) {
  if (draft) {
    className.value = base.value?.class_name ?? draft.className
    source.value = draft.source; kind.value = draft.kind; python.value = draft.python; requirements.value = draft.requirements
    return
  }
  if (base.value) {
    const v = base.value.latest
    const s = v.runtime_spec ?? {}
    className.value = base.value.class_name
    kind.value = s.type === 'container' ? 'container' : 'process'
    python.value = s.python_version ?? '3.12'
    requirements.value = (s.requirements ?? []).join('\n')
    try {
      source.value = (await api<{ text: string | null }>(`/functions/${v.function_id}/${v.version}/code`)).text ?? ''
    } catch { source.value = '' }
    return
  }
  className.value = 'ShoppingCart'; source.value = CLASS_STARTER; kind.value = 'process'; python.value = '3.12'; requirements.value = ''
}
onMounted(async () => {
  if (baseName.value) await store.load()
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
  const classes = [...source.value.matchAll(/^class\s+([A-Za-z_]\w*)/gm)].map(m => m[1]!)
  if (!base.value && classes.length && !classes.includes(className.value)) className.value = classes.at(-1)!
  ;(e.target as HTMLInputElement).value = ''
}

// ── register ───────────────────────────────────────────────────────────────
const registering = ref(false)
const registerError = ref<unknown>(null)
async function register() {
  if (!ready.value) { if (!check.value?.ok) runCheck(); return }
  if (check.value?.existing_version) { notifier.info(`No changes: the code is the same as v${check.value.existing_version}`); return }
  registering.value = true
  registerError.value = null
  const spec: Record<string, unknown> = { type: kind.value }
  if (kind.value === 'container') {
    spec.python_version = python.value
    spec.requirements = requirements.value.split('\n').map(r => r.trim()).filter(Boolean)
  }
  const form = new FormData()
  form.set('class_name', className.value)
  form.set('code_format', 'source')
  form.set('code_hash', check.value!.code_hash)
  form.set('runtime_spec', JSON.stringify(spec))
  form.set('code', new Blob([source.value], { type: 'text/x-python' }), `${className.value}.py`)
  try {
    const answer = await api<{ version: number; existing?: boolean }>('/object-classes', { method: 'POST', body: form })
    try { localStorage.removeItem(draftKey.value) } catch { /* nothing kept */ }
    restoring = true
    await store.load(true)
    if (answer.existing) notifier.info(`No changes: the code is the same as v${answer.version}`)
    else notifier.success(`${className.value} v${answer.version} registered`)
    router.push(`/ves/${id.value}/objects/classes/${encodeURIComponent(className.value)}`)
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
