<template>
  <div>
    <v-radio-group :model-value="modelValue === null ? 'all' : 'some'" inline hide-details density="compact" :disabled="readonly"
                   @update:model-value="$event === 'all' ? emit('update:modelValue', null) : emit('update:modelValue', modelValue ?? [])">
      <v-radio value="all" :label="allLabel" />
      <v-radio value="some" label="Only these files" />
    </v-radio-group>
    <v-expand-transition>
      <div v-if="modelValue !== null" class="mt-2">
        <v-autocomplete :model-value="picked" :items="choices" item-title="name" item-value="name" multiple chips closable-chips
                        density="compact" variant="outlined" label="Files" :loading="loading" hide-details="auto" :disabled="readonly"
                        :no-data-text="folder ? 'Nothing in this folder' : 'No files in this bucket'" @update:model-value="pick">
          <template #prepend-item>
            <v-text-field v-model="folder" density="compact" variant="plain" placeholder="Folder (e.g. raw/)" hide-details class="px-4"
                          prepend-inner-icon="mdi-folder-outline" @update:model-value="reload" />
            <v-divider />
          </template>
          <template #item="{ props: p, item }">
            <v-list-item v-bind="p" :subtitle="item.kind === 'folder' ? 'folder — type it above to open' : `v${item.version} · ${formatBytes(item.size ?? 0)}`"
                         :prepend-icon="item.kind === 'folder' ? 'mdi-folder' : 'mdi-file-outline'" :disabled="item.kind === 'folder'" />
          </template>
          <template #append-item>
            <v-list-item v-if="next" title="Load more" prepend-icon="mdi-chevron-down" @click="load(true)" />
          </template>
        </v-autocomplete>
        <div v-if="picked.length" class="text-caption axo-muted mt-1">{{ picked.length }} file{{ picked.length === 1 ? '' : 's' }} — the newest version of each when the run reads it.</div>
      </div>
    </v-expand-transition>
  </div>
</template>

<script setup lang="ts">
// Which files a bucket step reads: all of them, or the ones picked here
// (the step's `selected_items`, or a run's own `files`).
import type { FileEntry } from '~/utils/buckets'

export type PickedFiles = Array<{ name: string; version?: number | null }> | null
const props = withDefaults(defineProps<{ bucket: string; modelValue: PickedFiles; readonly?: boolean; allLabel?: string }>(), { allLabel: 'Every file' })
const emit = defineEmits<{ (e: 'update:modelValue', v: PickedFiles): void }>()
const { apiFetch } = useApi()

const files = ref<FileEntry[]>([])
const next = ref<string | null>(null)
const folder = ref('')
const loading = ref(false)
const picked = computed(() => (props.modelValue ?? []).map(f => f.name))
const choices = computed(() => {
  const listed = new Set(files.value.map(f => f.name))
  return [...files.value, ...picked.value.filter(n => !listed.has(n)).map(name => ({ kind: 'file', name } as FileEntry))]
})

async function load(more = false) {
  if (!props.bucket) return
  loading.value = true
  try {
    const page = await apiFetch<{ entries: FileEntry[]; next: string | null }>(`/buckets/${props.bucket}/objects`,
      { query: { prefix: folder.value || undefined, after: more ? next.value ?? undefined : undefined } })
    files.value = more ? [...files.value, ...page.entries] : page.entries
    next.value = page.next
  } catch { files.value = []; next.value = null } finally { loading.value = false }
}
let timer: ReturnType<typeof setTimeout> | undefined
function reload() { clearTimeout(timer); timer = setTimeout(() => load(), 300) }
function pick(names: string[]) {
  const before = new Map((props.modelValue ?? []).map(f => [f.name, f]))
  emit('update:modelValue', names.map(n => before.get(n) ?? { name: n }))
}
watch(() => props.bucket, () => { files.value = []; load() })
watch(() => props.modelValue !== null, (open) => { if (open && !files.value.length) load() }, { immediate: true })
</script>
