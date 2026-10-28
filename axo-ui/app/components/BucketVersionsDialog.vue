<template>
  <v-dialog :model-value="!!file" max-width="860" scrollable @update:model-value="!$event && emit('close')">
    <v-card v-if="file" rounded="lg">
      <v-card-title class="d-flex align-center ga-2 pt-4 px-6">
        <v-icon color="primary">mdi-history</v-icon>
        <span class="text-truncate">{{ baseName(file) }}</span>
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('close')" />
      </v-card-title>
      <div class="px-6 text-caption axo-muted">{{ bucket }}/{{ file }} · every version is kept until you delete it;
        reading without a version gets the newest complete one.</div>
      <v-card-text class="px-2">
        <AxoErrorAlert v-if="error" :error="error" title="The versions couldn't load" :retry="load" class="mx-4" />
        <AxoDataTable :headers="headers" :items="versions" item-value="version" :loading="loading" :searchable="false"
                      :items-per-page="-1" :pending="(v) => busy === v.version" flat>
          <template #item.version="{ item }">
            <span class="font-weight-medium text-no-wrap">v{{ item.version }}</span>
            <v-chip v-if="item.version === newest" size="x-small" variant="tonal" color="primary" class="ml-1">newest</v-chip>
          </template>
          <template #item.size="{ item }">{{ formatBytes(item.size) }}</template>
          <template #item.created_at="{ item }"><span :title="formatDateTime(item.created_at)">{{ timeAgo(item.created_at) }}</span></template>
          <template #item.by="{ item }"><BucketWriter :by="item.by" :function-name="functionName" /></template>
          <template #item.state="{ item }"><FileState :state="item.state" /></template>
          <template #item.content_hash="{ item }">
            <AxoCopy v-if="item.content_hash" :text="item.content_hash" max-width="110px" />
          </template>
          <template #item.tools="{ item }">
            <div v-if="item.state === 'complete'" class="d-flex justify-end">
              <v-btn icon="mdi-eye-outline" size="small" variant="text" :aria-label="`Preview version ${item.version}`" @click="preview(item)" />
              <v-btn icon="mdi-download" size="small" variant="text" :aria-label="`Download version ${item.version}`" @click="download(item)" />
              <v-btn v-if="canWrite" icon="mdi-delete-outline" size="small" variant="text" color="error"
                     :aria-label="`Delete version ${item.version}`" @click="remove(item)" />
            </div>
          </template>
        </AxoDataTable>
      </v-card-text>
    </v-card>
    <AxoFilePreview v-model="previewing" v-model:index="previewIndex" :files="previewFiles" />
  </v-dialog>
</template>

<script setup lang="ts">
import type { FileEntry } from '../utils/buckets'
import type { PreviewFile } from 'axo-ui-kit/app/components/AxoFilePreview.vue'

// A file's versions, newest first: size, when, who wrote it and its hash;
// each one can be previewed, downloaded or (Writers) deleted on its own.
const props = defineProps<{
  bucket: string; file: string | null; canWrite: boolean
  functionName?: (veId: string, functionId: string) => string | undefined
}>()
const emit = defineEmits<{ close: []; changed: [] }>()
const { apiFetch } = useApi()
const files = useBucketFiles()
const notify = useNotifier()
const confirmDialog = useConfirm()

const versions = ref<FileEntry[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const busy = ref<number | null>(null)
const newest = computed(() => Math.max(0, ...versions.value.map(v => v.version ?? 0)))
const headers = [
  { title: 'Version', key: 'version' }, { title: 'Size', key: 'size', align: 'end' as const },
  { title: 'Written', key: 'created_at' }, { title: 'By', key: 'by', sortable: false },
  { title: 'State', key: 'state' }, { title: 'Hash', key: 'content_hash', sortable: false },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]

async function load() {
  if (!props.file) return
  loading.value = true
  error.value = null
  try {
    versions.value = await apiFetch<FileEntry[]>(`/buckets/${props.bucket}/versions/${encodePath(props.file)}`)
  } catch (err) { error.value = err } finally { loading.value = false }
}
watch(() => props.file, (f) => { versions.value = []; if (f) load() }, { immediate: true })

const ref_ = (v: FileEntry) => ({ bucket: props.bucket, name: props.file!, version: v.version, size: v.size ?? undefined })
const download = (v: FileEntry) => files.download(ref_(v))

const previewing = ref(false)
const previewIndex = ref(0)
const previewFiles = computed<PreviewFile[]>(() => files.previewOf(
  versions.value.filter(v => v.state === 'complete').map(ref_)))
function preview(v: FileEntry) {
  previewIndex.value = Math.max(0, versions.value.filter(x => x.state === 'complete').indexOf(v))
  previewing.value = true
}

async function remove(v: FileEntry) {
  const last = versions.value.length === 1
  if (!await confirmDialog.confirm({
    title: `Delete version ${v.version} of ${baseName(props.file!)}?`, danger: true, confirmText: 'Delete version',
    text: last ? "It is the only version: the file goes with it. This can't be undone." : "The other versions stay. This can't be undone.",
  })) return
  busy.value = v.version ?? null
  try {
    await apiFetch(`/buckets/${props.bucket}/objects/${encodePath(props.file!)}`, { method: 'DELETE', query: { version: v.version } })
    versions.value = versions.value.filter(x => x.version !== v.version)
    notify.success(`Version ${v.version} deleted`)
    emit('changed')
    if (!versions.value.length) emit('close')
  } catch (err) { notify.error(err, "The version couldn't be deleted") } finally { busy.value = null }
}
</script>
