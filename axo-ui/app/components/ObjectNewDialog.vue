<template>
  <v-dialog :model-value="modelValue" max-width="720" scrollable @update:model-value="v => emit('update:modelValue', v)">
    <v-card>
      <v-card-title class="d-flex align-center ga-2 pt-4">
        <v-icon color="primary">mdi-shape-plus-outline</v-icon>New object
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('update:modelValue', false)" />
      </v-card-title>
      <v-card-text>
        <v-alert v-if="!store.classes.value.length" type="info" variant="tonal" density="compact">
          Register a class first: an object is made from one.
        </v-alert>
        <template v-else-if="!jobId || failedJob">
          <v-text-field v-model="name" label="Name" density="compact" variant="outlined" class="axo-mono mb-2" autofocus
                        :error-messages="nameError" hint="Unique in the VE; calls and the SDK use it (cart-42)." persistent-hint />
          <v-row density="compact" class="mt-1">
            <v-col cols="12" sm="7">
              <v-select v-model="className" :items="classChoices" label="Class" density="compact" variant="outlined" hide-details />
            </v-col>
            <v-col cols="12" sm="5">
              <v-select v-model="version" :items="versionChoices" label="Version" density="compact" variant="outlined" hide-details />
            </v-col>
          </v-row>
          <div class="text-subtitle-2 mt-4 mb-1">Values for <code class="axo-mono">__init__</code></div>
          <div v-if="!fields.length" class="text-body-2 axo-muted mb-2">{{ className }} v{{ version }} takes none.</div>
          <ParamField v-for="p in fields" :key="`${className}:${version}:${p.name}`" v-model="values[p.name]" :spec="p"
                      :error="fieldErrors[p.name]" @invalid="m => setInvalid(p.name, m)" @run="create" />
          <v-text-field v-model.number="keepRevisions" type="number" min="0" :max="maxKeep ?? undefined" label="Past revisions to keep"
                        density="compact" variant="outlined" class="mt-3" :placeholder="`${planKeep} (the plan's default)`" persistent-placeholder
                        :error-messages="keepError" :hint="maxKeep != null ? `Up to ${maxKeep} on this plan.` : 'Each kept revision can be restored.'"
                        persistent-hint />
          <AxoErrorAlert v-if="submitError" :error="submitError" title="The object couldn't be created" class="mt-3" />
          <v-alert v-if="failedJob" type="error" variant="tonal" density="compact" class="mt-3">
            __init__ failed, so {{ name }} wasn't created and the name is free again. Change the values and try again.
          </v-alert>
        </template>
        <template v-else>
          <div class="text-body-2 mb-2">Creating <b class="axo-mono">{{ name }}</b> ({{ className }} v{{ version }}): its <code>__init__</code> runs as a job.</div>
          <JobLive :key="jobId" :job-id="jobId" @finished="onFinished" />
        </template>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" class="text-none" @click="emit('update:modelValue', false)">{{ jobId && !failedJob ? 'Close' : 'Cancel' }}</v-btn>
        <AxoBtn v-if="!jobId || failedJob" prepend-icon="mdi-check" :loading="submitting" :disabled="!ready" @click="create">Create</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { Job } from '~/utils/jobs'

// A new object: its name, class + version (the newest by default), the
// values for __init__ (one field per parameter) and how many past revisions
// it keeps. __init__ runs live here; when it worked the object's page opens,
// when it failed the form comes back (the name is free again).
const props = defineProps<{ modelValue: boolean; className?: string | null; classVersion?: number | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()
const { id, ve, api } = useCurrentVe()
const store = useObjects()
const router = useRouter()
const notifier = useNotifier()

const name = ref('')
const className = ref('')
const version = ref(1)
const keepRevisions = ref<number | null>(null)
const classChoices = computed(() => store.classes.value.map(c => ({ value: c.class_name, title: c.class_name })))
const row = computed(() => store.classOf(className.value))
const versionChoices = computed(() => (row.value?.versions ?? []).map(v => ({ value: v.version,
  title: `v${v.version}${v.version === row.value?.latest.version ? ' (newest)' : ''}` })))
watch(className, () => { if (!row.value?.versions.some(v => v.version === version.value)) version.value = row.value?.latest.version ?? 1 })
const chosen = computed(() => row.value?.versions.find(v => v.version === version.value))
const fields = computed(() => methodFields(chosen.value?.init))

const limits = ref<Record<string, any>>({})
const planKeep = computed(() => limits.value.object_keep_revisions ?? 10)
const maxKeep = computed<number | null>(() => limits.value.object_max_keep_revisions ?? null)
const keepError = computed(() => (keepRevisions.value != null && (keepRevisions.value < 0 || (maxKeep.value != null && keepRevisions.value > maxKeep.value))
  ? `From 0 to ${maxKeep.value}` : undefined))
const nameError = computed(() => (name.value && store.objectOf(name.value) ? 'An object with this name exists' : name.value ? objectNameProblem(name.value) : undefined))

const values = reactive<Record<string, unknown>>({})
const invalid = reactive<Record<string, string>>({})
const fieldErrors = reactive<Record<string, string>>({})
const setInvalid = (n: string, m: string | null) => { if (m) invalid[n] = m; else delete invalid[n] }

const jobId = ref<string | null>(null)
const failedJob = ref(false)
const submitting = ref(false)
const submitError = ref<unknown>(null)
const ready = computed(() => !!name.value && !nameError.value && !keepError.value && !!chosen.value && !submitting.value)

watch(() => props.modelValue, async (open) => {
  if (!open) return
  jobId.value = null; failedJob.value = false; submitError.value = null; name.value = ''; keepRevisions.value = null
  for (const k of Object.keys(values)) delete values[k]
  await store.load()
  className.value = props.className ?? store.classes.value[0]?.class_name ?? ''
  version.value = props.classVersion ?? store.classOf(className.value)?.latest.version ?? 1
  try { limits.value = (await api<{ plan_limits: Record<string, any> }>('/ve')).plan_limits ?? {} } catch { /* defaults */ }
}, { immediate: true })

async function create() {
  if (!ready.value) return
  for (const k of Object.keys(fieldErrors)) delete fieldErrors[k]
  for (const p of fields.value) {
    if (p.required && (values[p.name] === undefined || values[p.name] === '')) fieldErrors[p.name] = 'Required'
    else if (invalid[p.name]) fieldErrors[p.name] = invalid[p.name]!
  }
  if (Object.keys(fieldErrors).length) return
  submitting.value = true
  submitError.value = null
  const kwargs = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== undefined))
  try {
    const started = await api<{ job_id: string }>('/objects', { method: 'POST', body: {
      name: name.value, class_name: className.value, class_version: version.value, kwargs,
      ...(keepRevisions.value != null ? { keep_revisions: keepRevisions.value } : {}) } })
    failedJob.value = false
    jobId.value = started.job_id
    store.load(true)
  } catch (err) {
    submitError.value = err
  } finally {
    submitting.value = false
  }
}

async function onFinished(job: Job) {
  if (job.status !== 'COMPLETED') { failedJob.value = true; store.load(true); return }
  await store.load(true)
  notifier.success(`${name.value} created in ${ve.value?.name ?? 'the VE'}`)
  emit('update:modelValue', false)
  router.push(`/ves/${id.value}/objects/${encodeURIComponent(name.value)}`)
}
</script>
