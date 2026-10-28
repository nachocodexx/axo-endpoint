<template>
  <div class="d-flex flex-wrap ga-2">
    <v-autocomplete :model-value="bucket" :items="buckets" item-title="name" item-value="name" density="compact" variant="outlined"
                    :label="`${label} · bucket`" :loading="loadingBuckets" hide-details="auto" class="axo-pick"
                    :error-messages="error" no-data-text="No bucket you can read" @update:model-value="pickBucket" />
    <v-autocomplete :model-value="modelValue?.name ?? null" :items="items" item-title="name" item-value="name" density="compact"
                    variant="outlined" label="File" :loading="loadingFiles" :disabled="!bucket" hide-details="auto" class="axo-pick"
                    :no-data-text="folder ? 'Nothing in this folder' : 'No files in this bucket'" @update:model-value="pickFile">
      <template #item="{ props: item, item: f }">
        <v-list-item v-if="f.name === UP" v-bind="item" title="Up one folder" :subtitle="folder" prepend-icon="mdi-arrow-up" />
        <v-list-item v-else-if="f.kind === 'folder'" v-bind="item" :title="baseName(f.name)"
                     :subtitle="`${f.files ?? 0} file${f.files === 1 ? '' : 's'}`" prepend-icon="mdi-folder" />
        <v-list-item v-else v-bind="item" :title="baseName(f.name)" :subtitle="`v${f.version} · ${formatBytes(f.size)}`"
                     prepend-icon="mdi-file-outline" />
      </template>
      <template #append-item>
        <v-list-item v-if="next" title="Load more" prepend-icon="mdi-chevron-down" @click="loadFiles(true)" />
      </template>
    </v-autocomplete>
    <v-text-field :model-value="modelValue?.version ?? ''" type="number" density="compact" variant="outlined" label="Version"
                  placeholder="newest" hide-details persistent-placeholder class="axo-pick axo-pick--version" :disabled="!modelValue?.name"
                  @update:model-value="pickVersion" />
  </div>
</template>

<script setup lang="ts">
// A file in a bucket, for a `DataRef` parameter: bucket → file → version
// (empty = the newest one when the job reads it). Lists only buckets this
// person can read. Files are browsed one folder at a time: picking a
// folder opens it.
import type { FileEntry } from '../utils/buckets'

export interface DataRefValue { bucket: string; name: string; version?: number | null; format?: string }
const props = defineProps<{ modelValue: DataRefValue | null; label: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: DataRefValue | null): void }>()
const { apiFetch } = useApi()

const buckets = ref<Array<{ name: string }>>([])
const files = ref<FileEntry[]>([])
const folder = ref('')
const next = ref<string | null>(null)
const UP = '\u0000up'
const items = computed(() => [...(folder.value ? [{ kind: 'folder', name: UP } as FileEntry] : []), ...files.value])
const bucket = ref<string | null>(props.modelValue?.bucket ?? null)
const loadingBuckets = ref(false)
const loadingFiles = ref(false)
const error = ref<string | undefined>()

onMounted(async () => {
  loadingBuckets.value = true
  try {
    buckets.value = await apiFetch('/buckets')
  } catch { error.value = "Buckets couldn't load" } finally { loadingBuckets.value = false }
  const name = props.modelValue?.name ?? ''
  folder.value = name.includes('/') ? name.slice(0, name.lastIndexOf('/') + 1) : ''
  if (bucket.value) loadFiles()
})

async function loadFiles(more = false) {
  if (!bucket.value) return
  loadingFiles.value = true
  try {
    const page = await apiFetch<{ entries: FileEntry[]; next: string | null }>(`/buckets/${bucket.value}/objects`,
      { query: { prefix: folder.value, after: more ? next.value ?? undefined : undefined } })
    files.value = more ? [...files.value, ...page.entries] : page.entries
    next.value = page.next
  } catch { files.value = []; next.value = null } finally { loadingFiles.value = false }
}
function pickBucket(b: string | null) {
  bucket.value = b
  files.value = []
  folder.value = ''
  emit('update:modelValue', null)
  loadFiles()
}
function open(path: string) {
  folder.value = path
  files.value = []
  emit('update:modelValue', null)
  loadFiles()
}
function pickFile(name: string | null) {
  if (name === UP) return open(folder.value.replace(/[^/]+\/$/, ''))
  if (name?.endsWith('/')) return open(name)
  emit('update:modelValue', name && bucket.value ? { bucket: bucket.value, name } : null)
}
function pickVersion(v: string | number) {
  if (!props.modelValue) return
  const n = Number(v)
  emit('update:modelValue', { ...props.modelValue, version: v === '' || !Number.isFinite(n) || n < 1 ? null : Math.floor(n) })
}
</script>

<style scoped>
.axo-pick { flex: 1 1 200px; }
.axo-pick--version { flex: 0 1 120px; }
</style>
