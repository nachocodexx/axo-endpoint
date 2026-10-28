<template>
  <v-dialog :model-value="modelValue" max-width="760" scrollable @update:model-value="v => emit('update:modelValue', v)">
    <v-card>
      <v-card-title class="d-flex align-center ga-2 pt-4">
        <v-icon color="primary">mdi-play-circle-outline</v-icon>
        <span class="text-truncate">Call {{ object?.name }}</span>
        <span v-if="object" class="text-body-2 axo-muted">{{ object.class_name }} v{{ object.class_version }}</span>
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('update:modelValue', false)" />
      </v-card-title>

      <v-card-text>
        <v-alert v-if="!cls" type="warning" variant="tonal" density="compact">
          The class version of this object isn't listed: it may have been deleted.
        </v-alert>
        <template v-else>
          <v-select v-model="method" :items="methodChoices" label="Method" density="compact" variant="outlined"
                    :hint="chosen?.doc ?? undefined" persistent-hint class="mb-4" :disabled="!!jobId && !done" />

          <template v-if="!jobId || done">
            <div v-if="!fields.length" class="text-body-2 axo-muted mb-2">{{ method }} takes no parameters.</div>
            <ParamField v-for="p in fields" :key="`${method}:${p.name}`" v-model="values[p.name]" :spec="p" :error="fieldErrors[p.name]"
                        @invalid="m => setInvalid(p.name, m)" @run="call" />

            <v-expansion-panels v-model="moreOpen" flat class="mt-3">
              <v-expansion-panel value="more" class="axo-options">
                <v-expansion-panel-title class="px-0 text-body-2">
                  <v-icon size="18" class="mr-2">mdi-cog-outline</v-icon>More
                  <span class="text-caption axo-muted ml-2">{{ moreSummary }}</span>
                </v-expansion-panel-title>
                <v-expansion-panel-text class="axo-options-text">
                  <v-row density="compact">
                    <v-col cols="12" sm="6">
                      <v-select v-model="keep" :items="keepChoices" label="Keep the result for" density="compact" variant="outlined" hide-details />
                    </v-col>
                    <v-col cols="12" sm="6">
                      <v-select v-model="maxWait" :items="WAITS" label="Wait for a free runner" density="compact" variant="outlined" hide-details />
                    </v-col>
                  </v-row>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
            <AxoErrorAlert v-if="submitError" :error="submitError" title="The call couldn't start" class="mt-3" />
          </template>

          <template v-if="jobId">
            <div v-if="ahead && !done" class="text-body-2 axo-muted mb-2">
              <v-icon size="16" class="mr-1">mdi-tray-full</v-icon>
              {{ ahead }} call{{ ahead === 1 ? '' : 's' }} before this one: an object runs one call at a time.
            </div>
            <JobLive :key="jobId" :job-id="jobId" class="mt-2" @finished="onFinished" />
            <div v-if="done && outcome" class="mt-3 text-body-2">
              <template v-if="outcome.meta.outcome === 'completed'">
                <template v-if="outcome.meta.changed?.length">
                  <v-icon size="16" color="success" class="mr-1">mdi-content-save-check-outline</v-icon>
                  Saved as revision {{ outcome.meta.revision }}: changed
                  <code class="axo-mono">{{ (outcome.meta.changed as string[]).join(', ') }}</code>
                  <template v-if="outcome.meta.deleted?.length"> · removed <code v-for="d in outcome.meta.deleted" :key="d" class="axo-mono mr-1">{{ d }}</code></template>
                </template>
                <template v-else>Nothing changed: the object stays at revision {{ outcome.meta.revision }}.</template>
              </template>
              <template v-else>Nothing was saved: the object is as it was.</template>
            </div>
          </template>
        </template>
      </v-card-text>

      <v-card-actions class="px-6 pb-4">
        <span v-if="!can('developer')" class="text-caption axo-muted">Developers and above call objects.</span>
        <span v-else-if="!jobId || done" class="text-caption axo-muted">Ctrl + Enter</span>
        <v-spacer />
        <v-btn variant="text" class="text-none" @click="emit('update:modelValue', false)">Close</v-btn>
        <AxoBtn v-if="!jobId || done" prepend-icon="mdi-play" :loading="submitting" :disabled="!can('developer') || !cls" @click="call">
          {{ done ? 'Call again' : 'Call' }}
        </AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { Job, JobListRow } from '~/utils/jobs'
import type { ActivityRow } from '~/utils/activity'

// Calls a method of an object: pick it, one field per parameter (the code's
// defaults shown), "More" (result keep time, how long to wait for a runner).
// The dialog stays: the call is followed live (the calls queued before it,
// then its result or error), then what it changed and the new revision.
// "Call again" keeps the values. `called` fires when one finishes.
const props = defineProps<{ modelValue: boolean; objectName: string; method?: string | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'called', job: Job): void }>()
const { can, api } = useCurrentVe()
const store = useObjects()

const object = computed(() => store.objectOf(props.objectName))
const cls = computed(() => object.value && store.versionOf(object.value.class_name, object.value.class_version))
const methodChoices = computed(() => (cls.value?.methods ?? []).map(m => ({ value: m.name, title: m.name })))
const method = ref<string>('run')
const chosen = computed(() => cls.value?.methods.find(m => m.name === method.value))
const fields = computed(() => methodFields(chosen.value))

const values = reactive<Record<string, unknown>>({})
const invalid = reactive<Record<string, string>>({})
const fieldErrors = reactive<Record<string, string>>({})
const setInvalid = (name: string, m: string | null) => { if (m) invalid[name] = m; else delete invalid[name] }
watch(method, () => { for (const k of Object.keys(values)) delete values[k]; for (const k of Object.keys(fieldErrors)) delete fieldErrors[k] })

const moreOpen = ref<string[]>([])
const keep = ref<string>('')
const maxWait = ref<number | null>(null)
const veSettings = ref<{ result_keep_max?: string; result_keep_default?: string } | null>(null)
const WAITS = [{ value: null, title: 'The VE’s default' }, { value: -1, title: 'As long as needed' }, { value: 0, title: 'Not at all (fail at once)' },
  { value: 60, title: '1 minute' }, { value: 600, title: '10 minutes' }, { value: 3600, title: '1 hour' }]
const keepChoices = computed(() => [{ value: '', title: `The default (${keepTitle(veSettings.value?.result_keep_default ?? '7d')})` },
  ...keepUpTo(veSettings.value?.result_keep_max)])
const moreSummary = computed(() => [keep.value ? `result kept ${keepTitle(keep.value).toLowerCase()}` : null,
  maxWait.value != null ? WAITS.find(w => w.value === maxWait.value)?.title.toLowerCase() : null].filter(Boolean).join(' · '))

// ── each time it opens ─────────────────────────────────────────────────────
const jobId = ref<string | null>(null)
const done = ref(false)
const ahead = ref(0)
const outcome = ref<ActivityRow | null>(null)
const submitError = ref<unknown>(null)
watch(() => props.modelValue, async (open) => {
  if (!open) return
  jobId.value = null; done.value = false; outcome.value = null; submitError.value = null
  await store.load()
  const names = cls.value?.methods.map(m => m.name) ?? []
  method.value = props.method && names.includes(props.method) ? props.method : names.includes('run') ? 'run' : names[0] ?? 'run'
  if (!veSettings.value) { try { veSettings.value = (await api<{ settings: any }>('/ve')).settings } catch { /* defaults */ } }
}, { immediate: true })

function check(): boolean {
  for (const k of Object.keys(fieldErrors)) delete fieldErrors[k]
  let ok = true
  for (const p of fields.value) {
    const v = values[p.name]
    if (p.required && (v === undefined || v === '' || v === null)) { fieldErrors[p.name] = 'Required'; ok = false }
    else if (invalid[p.name]) { fieldErrors[p.name] = invalid[p.name]!; ok = false }
  }
  return ok
}

const submitting = ref(false)
async function call() {
  if (!can('developer') || submitting.value || (jobId.value && !done.value) || !check()) return
  submitting.value = true
  submitError.value = null
  const params = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== undefined))
  const body: Record<string, unknown> = { method: method.value, params }
  if (keep.value) body.keep_result = keep.value
  if (maxWait.value != null) body.max_wait = maxWait.value
  try {
    const started = await api<{ job_id: string }>(`/objects/${encodeURIComponent(props.objectName)}/calls`, { method: 'POST', body })
    outcome.value = null
    done.value = false
    jobId.value = started.job_id
    countAhead(started.job_id)
  } catch (err) {
    submitError.value = err
  } finally {
    submitting.value = false
  }
}

/** The object's calls still waiting or running, started before this one. */
async function countAhead(mine: string) {
  try {
    const page = await api<{ rows: JobListRow[] }>('/jobs?status=QUEUED,RUNNING,WAITING_RETRY&limit=100')
    const jobs = page.rows.flatMap(r => (r.type === 'job' ? [r.job] : r.jobs))
    const me = jobs.find(j => j.job_id === mine)
    ahead.value = jobs.filter(j => j.job_id !== mine && j.kind === 'object' && j.object_name === props.objectName
      && (!me || ts(j.created_at)! <= ts(me.created_at)!)).length
  } catch { ahead.value = 0 }
}

/** What it changed: the object's history row for this job (recorded a moment after). */
async function onFinished(job: Job) {
  done.value = true
  emit('called', job)
  for (let tries = 0; tries < 6 && jobId.value === job.job_id; tries++) {
    try {
      const rows = await api<ActivityRow[]>(`/objects/${encodeURIComponent(props.objectName)}/history?limit=20`)
      const row = rows.find(r => r.event_type === 'ActiveObjectCalled' && r.meta?.job_id === job.job_id)
      if (row) { outcome.value = row; return }
    } catch { /* tried again */ }
    await new Promise(r => setTimeout(r, 1000))
  }
}
</script>
