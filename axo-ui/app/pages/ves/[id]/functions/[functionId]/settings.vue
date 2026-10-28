<template>
  <div>
    <template v-if="latest">
    <v-alert v-if="!can('developer')" type="info" variant="tonal" density="compact" class="mb-4">
      Developers and above change a function's settings.
    </v-alert>
    <template v-else>
      <div class="text-body-2 axo-muted mb-4">
        These settings belong to v{{ latest.version }} (the newest) and apply to its next jobs; the code doesn't change and no new version is made.
      </div>

      <AxoPanel title="Parameters" icon="mdi-form-textbox" stacked class="mb-4"
                help="Read from the code when it was registered. Change how the run form shows them: the type, whether one is required, its default and its text.">
        <div v-if="!params.length" class="text-body-2 axo-muted">It takes no parameters.</div>
        <div v-for="(p, i) in params" :key="p.name" class="axo-param-row">
          <div class="axo-mono text-body-2 font-weight-medium axo-param-name">{{ p.name }}</div>
          <v-select v-model="p.type" :items="TYPES" label="Type" density="compact" variant="outlined" hide-details class="axo-param-type" />
          <v-checkbox v-model="p.required" label="Required" density="compact" hide-details class="flex-grow-0" />
          <v-text-field v-model="defaults[i]" label="Default (JSON)" density="compact" variant="outlined" :disabled="p.required"
                        :error-messages="defaultError(i)" hide-details="auto" class="axo-param-default axo-mono" />
          <v-text-field v-model="p.description" label="What it is" density="compact" variant="outlined" hide-details class="axo-param-text" />
        </div>
      </AxoPanel>

      <AxoPanel title="Environment variables" icon="mdi-variable" stacked class="mb-4"
                help="Given to the code as environment variables. A secret's value is never shown again, to anyone; leave it empty to keep it.">
        <div v-for="(e, i) in env" :key="i" class="d-flex flex-wrap align-center ga-2 mb-2">
          <v-text-field v-model="e.name" label="Name" density="compact" variant="outlined" hide-details="auto" class="axo-env-name axo-mono"
                        :error-messages="envNameError(i)" />
          <v-text-field v-model="e.value" :label="e.secret ? 'Value (secret)' : 'Value'" density="compact" variant="outlined"
                        :type="e.secret ? 'password' : 'text'" hide-details class="axo-env-value axo-mono"
                        :placeholder="e.secret && e.kept ? 'Kept — type to replace' : ''" persistent-placeholder />
          <v-checkbox v-model="e.secret" label="Secret" density="compact" hide-details class="flex-grow-0" :disabled="e.kept && e.secret"
                      :title="e.kept && e.secret ? 'A secret stays secret; remove it to make a plain one' : undefined" />
          <v-btn icon="mdi-close" size="small" variant="text" :aria-label="`Remove ${e.name || 'variable'}`" @click="env.splice(i, 1)" />
        </div>
        <v-btn size="small" variant="tonal" class="text-none" prepend-icon="mdi-plus" @click="env.push({ name: '', value: '', secret: false, kept: false })">
          Add a variable
        </v-btn>
      </AxoPanel>

      <AxoPanel title="Running" icon="mdi-cog-outline" stacked class="mb-4">
        <v-row density="compact">
          <v-col cols="12" sm="4">
            <v-text-field v-model.number="run.max_attempts" type="number" min="1" max="10" label="Attempts in all" density="compact"
                          variant="outlined" :error-messages="attemptsError" hint="When the code fails. Platform failures are tried again free."
                          persistent-hint />
          </v-col>
          <v-col cols="12" sm="4">
            <v-text-field v-model.number="run.retry_delay_seconds" type="number" min="0" label="Wait before trying again (s)" density="compact"
                          variant="outlined" :disabled="run.max_attempts <= 1" hide-details />
          </v-col>
          <v-col cols="12" sm="4">
            <v-select v-model="run.retry_backoff" :items="BACKOFFS" label="Each wait" density="compact" variant="outlined"
                      :disabled="run.max_attempts <= 1" hide-details />
          </v-col>
          <v-col cols="12" sm="6">
            <v-select v-model="run.keep_result" :items="keepChoices" label="Keep results for" density="compact" variant="outlined"
                      hint="Per job it can be changed in Run options." persistent-hint />
          </v-col>
          <template v-if="isContainer">
            <v-col cols="12" sm="3">
              <v-text-field v-model.number="run.max_concurrency" type="number" min="1" max="64" label="Runners at most" density="compact"
                            variant="outlined" hint="In the whole VE." persistent-hint />
            </v-col>
            <v-col cols="12" sm="3">
              <v-select v-model="run.idle_ttl_seconds" :items="IDLE" label="Stop a runner idle for" density="compact" variant="outlined"
                        hide-details />
            </v-col>
            <v-col cols="12">
              <RunnerSizeFields v-model:cpu="run.cpu_limit" v-model:memory-mb="run.memory_mb" />
              <div class="text-caption axo-muted mt-1">A new size applies to runners started from now on.</div>
            </v-col>
          </template>
        </v-row>
      </AxoPanel>

      <AxoErrorAlert v-if="saveError" :error="saveError" title="The settings couldn't be saved" class="mb-3" />
      <div class="d-flex align-center ga-2 axo-save-bar">
        <AxoBtn prepend-icon="mdi-content-save" :loading="saving" :disabled="!changed || invalid" @click="save">Save</AxoBtn>
        <v-btn variant="text" class="text-none" :disabled="!changed || saving" @click="reset">Undo changes</v-btn>
        <span v-if="changed" class="text-caption axo-muted">Not saved yet</span>
      </div>
    </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { ParamSpec } from '~/utils/functions'

// The newest version's settings, saved in place (no new version): the run
// form's parameters, environment variables (secrets never come back), and
// attempts / result keep time / runners (how many, idle time, size).
const { can, api } = useCurrentVe()
const { functionId, latest, functions } = useCurrentFunction()
const notifier = useNotifier()

const TYPES = [{ value: 'number', title: 'Number' }, { value: 'string', title: 'Text' }, { value: 'boolean', title: 'Yes / no' },
  { value: 'json', title: 'JSON' }, { value: 'data_ref', title: 'Bucket file' }]
const BACKOFFS = [{ value: 'fixed', title: 'The same' }, { value: 'exponential', title: 'Twice as long' }]
const IDLE = [{ value: null, title: 'The endpoint’s default' }, { value: 60, title: '1 minute' }, { value: 300, title: '5 minutes' },
  { value: 1800, title: '30 minutes' }, { value: 3600, title: '1 hour' }, { value: -1, title: 'Never (always warm)' }]

interface EnvRow { name: string; value: string; secret: boolean; kept: boolean }
const params = ref<ParamSpec[]>([])
const defaults = ref<string[]>([])
const env = ref<EnvRow[]>([])
const run = reactive({ max_attempts: 1, retry_delay_seconds: 1, retry_backoff: 'fixed', keep_result: null as string | null,
  max_concurrency: 1, idle_ttl_seconds: null as number | null, cpu_limit: null as number | null, memory_mb: null as number | null })
const isContainer = computed(() => latest.value?.runtime_spec?.type === 'container')

const veSettings = ref<{ result_keep_max?: string; result_keep_default?: string } | null>(null)
onMounted(async () => { try { veSettings.value = (await api<{ settings: any }>('/ve')).settings } catch { /* defaults */ } })
const keepChoices = computed(() => [{ value: null, title: `The VE’s default (${keepTitle(veSettings.value?.result_keep_default ?? '7d')})` },
  ...keepUpTo(veSettings.value?.result_keep_max)])

let original = ''
const snapshot = () => JSON.stringify({ params: params.value, defaults: defaults.value, env: env.value, run })
function reset() {
  const v = latest.value
  if (!v) return
  const s = v.runtime_spec ?? { type: 'process' }
  params.value = (v.params_schema ?? []).map(p => ({ ...p, description: p.description ?? '' }))
  defaults.value = params.value.map(p => (p.required || p.default === undefined ? '' : JSON.stringify(p.default)))
  const secrets = new Set(s.secret_env ?? [])
  env.value = Object.entries(s.env_vars ?? {}).map(([name, value]) => ({ name, value: secrets.has(name) ? '' : value ?? '',
    secret: secrets.has(name), kept: true }))
  Object.assign(run, { max_attempts: s.max_attempts ?? 1, retry_delay_seconds: s.retry_delay_seconds ?? 1, retry_backoff: s.retry_backoff ?? 'fixed',
    keep_result: s.keep_result ?? null, max_concurrency: s.max_concurrency ?? 1, idle_ttl_seconds: s.idle_ttl_seconds ?? null,
    cpu_limit: s.cpu_limit ?? null, memory_mb: s.memory_limit_bytes ? Math.round(s.memory_limit_bytes / 1024 / 1024) : null })
  original = snapshot()
  tick.value++
}
const tick = ref(0)
watch(() => latest.value && `${latest.value.function_id}:${latest.value.version}`, reset, { immediate: true })
const changed = computed(() => { void tick.value; return snapshot() !== original })

function defaultError(i: number): string | undefined {
  const text = defaults.value[i]
  if (!text || params.value[i]?.required) return undefined
  try { JSON.parse(text); return undefined } catch { return 'Not JSON (text goes in "quotes")' }
}
function envNameError(i: number): string | undefined {
  const name = env.value[i]!.name
  if (!name) return 'A name'
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return 'Letters, digits and _'
  if (env.value.findIndex(e => e.name === name) !== i) return 'Used twice'
  return undefined
}
const attemptsError = computed(() => (run.max_attempts < 1 || run.max_attempts > 10 ? 'From 1 to 10' : undefined))
const invalid = computed(() => !!attemptsError.value || params.value.some((_, i) => defaultError(i)) || env.value.some((_, i) => envNameError(i)))

const saving = ref(false)
const saveError = ref<unknown>(null)
async function save() {
  if (!latest.value || invalid.value) return
  saving.value = true
  saveError.value = null
  const schema = params.value.map((p, i) => {
    const out: Record<string, unknown> = { name: p.name, type: p.type, required: p.required, description: p.description || null }
    if (!p.required && defaults.value[i]) out.default = JSON.parse(defaults.value[i]!)
    return out
  })
  const runtime: Record<string, unknown> = { max_attempts: run.max_attempts, retry_delay_seconds: run.retry_delay_seconds,
    retry_backoff: run.retry_backoff, keep_result: run.keep_result }
  if (isContainer.value) {
    Object.assign(runtime, { max_concurrency: run.max_concurrency, idle_ttl_seconds: run.idle_ttl_seconds,
      // null = the plan's default again (the VE fills it in)
      cpu_limit: run.cpu_limit, memory_limit_bytes: run.memory_mb != null ? run.memory_mb * 1024 * 1024 : null })
  }
  try {
    await api(`/functions/${functionId.value}/${latest.value.version}`, { method: 'PATCH', body: {
      params_schema: schema, params_schema_replace: true,
      // a kept secret left empty is sent as null: its value stays
      env_vars: Object.fromEntries(env.value.map(e => [e.name, e.secret && e.kept && !e.value ? null : e.value])),
      env_vars_replace: true, secret_env: env.value.filter(e => e.secret).map(e => e.name),
      runtime,
    } })
    notifier.success('Settings saved')
    await functions.load(true)
    reset()
  } catch (err) { saveError.value = err } finally { saving.value = false }
}
onBeforeRouteLeave(() => (!changed.value || window.confirm('Leave without saving the settings?')))
</script>

<style scoped>
.axo-param-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 0; }
.axo-param-row + .axo-param-row { border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-param-name { flex: 0 0 140px; }
.axo-param-type { flex: 0 1 150px; }
.axo-param-default { flex: 0 1 160px; }
.axo-param-text { flex: 1 1 220px; }
.axo-env-name { flex: 0 1 220px; }
.axo-env-value { flex: 1 1 240px; }
.axo-save-bar { position: sticky; bottom: 0; padding: 12px 0; background: rgb(var(--v-theme-background)); z-index: 1; }
@media (max-width: 600px) { .axo-param-name { flex-basis: 100%; } }
</style>
