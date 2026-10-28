<template>
  <v-dialog :model-value="modelValue" max-width="520" @update:model-value="v => emit('update:modelValue', v)">
    <v-card>
      <v-card-title class="d-flex align-center ga-2 pt-4">
        <v-icon color="primary">{{ mode === 'clone' ? 'mdi-content-copy' : 'mdi-arrow-up-bold-circle-outline' }}</v-icon>
        {{ mode === 'clone' ? `Clone ${object?.name}` : `Upgrade ${object?.name}` }}
      </v-card-title>
      <v-card-text>
        <template v-if="mode === 'clone'">
          <div class="text-body-2 mb-3">A new object with a copy of what {{ object?.name }} holds now (no code runs).
            Equal values are stored once, so a clone costs nothing until one of them changes.</div>
          <v-text-field v-model="name" label="New name" density="compact" variant="outlined" class="axo-mono" autofocus
                        :error-messages="nameError" @keyup.enter="go" />
        </template>
        <template v-else>
          <div v-if="!newer.length" class="text-body-2">{{ object?.name }} is on the newest version of {{ object?.class_name }}.</div>
          <template v-else>
            <div class="text-body-2 mb-3">
              From v{{ object?.class_version }} to a newer version of {{ object?.class_name }}. When that version has an
              <code>upgrade(self, old_version)</code> step, it runs first (as a job); the object keeps its values otherwise.
            </div>
            <v-select v-model="target" :items="newer" label="Version" density="compact" variant="outlined" hide-details />
            <div class="text-caption axo-muted mt-2">{{ targetDoc?.has_upgrade ? 'This version has an upgrade step.' : 'No upgrade step: only the code changes.' }}</div>
          </template>
        </template>
        <AxoErrorAlert v-if="error" :error="error" :title="mode === 'clone' ? 'It couldn’t be cloned' : 'It couldn’t be upgraded'" class="mt-3" />
        <JobLive v-if="jobId" :key="jobId" :job-id="jobId" class="mt-3" @finished="onFinished" />
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" class="text-none" @click="emit('update:modelValue', false)">{{ jobId ? 'Close' : 'Cancel' }}</v-btn>
        <AxoBtn v-if="!jobId" :loading="busy" :disabled="mode === 'clone' ? !name || !!nameError : !newer.length" @click="go">
          {{ mode === 'clone' ? 'Clone' : `Upgrade to v${target}` }}
        </AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { Job } from '~/utils/jobs'

// Clone (a new name, at once) or upgrade (to a newer version of its class,
// run as a job and followed here) one object.
const props = defineProps<{ modelValue: boolean; objectName: string; mode: 'clone' | 'upgrade' }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'done'): void }>()
const { id, api } = useCurrentVe()
const store = useObjects()
const router = useRouter()
const notifier = useNotifier()

const object = computed(() => store.objectOf(props.objectName))
const name = ref('')
const nameError = computed(() => (name.value && store.objectOf(name.value) ? 'An object with this name exists' : name.value ? objectNameProblem(name.value) : undefined))
const newer = computed(() => (store.classOf(object.value?.class_name ?? '')?.versions ?? [])
  .filter(v => v.version > (object.value?.class_version ?? 0)).map(v => ({ value: v.version, title: `v${v.version}` })))
const target = ref(0)
const targetDoc = computed(() => store.versionOf(object.value?.class_name ?? '', target.value))
const busy = ref(false)
const error = ref<unknown>(null)
const jobId = ref<string | null>(null)

watch(() => props.modelValue, (open) => {
  if (!open) return
  error.value = null; jobId.value = null
  name.value = `${props.objectName}-copy`
  target.value = newer.value[0]?.value ?? 0
  if (newer.value.length) target.value = Math.max(...newer.value.map(v => v.value))
}, { immediate: true })

async function go() {
  busy.value = true
  error.value = null
  const base = `/objects/${encodeURIComponent(props.objectName)}`
  try {
    if (props.mode === 'clone') {
      if (!name.value || nameError.value) return
      await api(`${base}/clone`, { method: 'POST', body: { name: name.value } })
      await store.load(true)
      notifier.success(`${name.value} made from ${props.objectName}`)
      emit('update:modelValue', false)
      emit('done')
      router.push(`/ves/${id.value}/objects/${encodeURIComponent(name.value)}`)
    } else {
      const started = await api<{ job_id: string }>(`${base}/upgrade`, { method: 'POST', body: { class_version: target.value } })
      jobId.value = started.job_id
    }
  } catch (err) {
    error.value = err
  } finally {
    busy.value = false
  }
}

async function onFinished(job: Job) {
  await store.load(true)
  emit('done')
  if (job.status === 'COMPLETED') notifier.success(`${props.objectName} is on v${target.value}`)
}
</script>
