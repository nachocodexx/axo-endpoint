<template>
  <div class="axo-drop-area" @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave="onDragLeave" @drop.prevent="onDrop">
    <!-- Where you are: the bucket, then each folder -->
    <div class="d-flex align-center flex-wrap ga-1 mb-3">
      <v-btn v-for="(c, i) in crumbs" :key="c.path" variant="text" size="small" class="text-none px-2"
             :class="i === crumbs.length - 1 ? 'font-weight-bold' : 'axo-muted'" :prepend-icon="i === 0 ? 'mdi-database-outline' : undefined"
             :to="bucketLink(name, '', c.path)" exact>{{ c.title }}</v-btn>
    </div>

    <AxoErrorAlert v-if="error" :error="error" title="The files couldn't load" :retry="reload" class="mb-4" />

    <AxoDataTable :headers="headers" :items="entries" item-value="name" :loading="loading" :searchable="false"
                  :items-per-page="-1" :selectable="true" v-model:selected="selected"
                  :clickable="() => true" @row="open">
      <template #filters>
        <v-text-field v-model="search" prepend-inner-icon="mdi-magnify" placeholder="Names starting with…" hide-details
                      density="compact" clearable style="max-width: 300px; min-width: 200px" />
      </template>
      <template #actions>
        <AxoBtn v-if="selected.length" look="plain" prepend-icon="mdi-download" :loading="downloading" @click="downloadSelected">
          Download {{ selectedText }}
        </AxoBtn>
        <v-menu v-if="can('writer')" location="bottom end">
          <template #activator="{ props: menu }">
            <AxoBtn v-bind="menu" prepend-icon="mdi-upload">Upload</AxoBtn>
          </template>
          <v-list density="compact" rounded="lg">
            <v-list-item prepend-icon="mdi-file-upload-outline" title="Files…" @click="pickFiles?.click()" />
            <v-list-item prepend-icon="mdi-folder-upload-outline" title="A folder…" @click="pickFolder?.click()" />
          </v-list>
        </v-menu>
        <input ref="pickFiles" type="file" multiple hidden @change="onPicked">
        <input ref="pickFolder" type="file" webkitdirectory hidden @change="onPicked">
      </template>
      <template #empty>
        <AxoEmptyState :icon="search ? 'mdi-file-search-outline' : 'mdi-folder-open-outline'"
                       :title="search ? 'No names start with that' : folder ? 'This folder is empty' : 'No files yet'"
                       :text="search ? 'Search finds names that start with what you type, inside this folder.' : can('writer') ? 'Drop files or a folder here, or use Upload. Jobs can write files here too.' : 'Files that people and jobs write here show up in this list.'" />
      </template>
      <template #item.name="{ item }">
        <div class="d-flex align-center ga-2 py-1">
          <v-icon :color="item.kind === 'folder' ? 'primary' : undefined" size="20">{{ iconOf(item) }}</v-icon>
          <span :class="item.kind === 'folder' ? 'font-weight-medium' : ''" class="text-truncate" style="max-width: 420px">{{ baseName(item.name) }}</span>
        </div>
      </template>
      <template #item.size="{ item }">
        <span v-if="item.kind === 'folder'" class="axo-muted text-no-wrap">{{ formatNumber(item.files ?? 0, false) }} file{{ item.files === 1 ? '' : 's' }}</span>
        <span v-else class="text-no-wrap">{{ formatBytes(item.size) }}</span>
      </template>
      <template #item.versions="{ item }">{{ item.kind === 'file' ? item.versions : '' }}</template>
      <template #item.created_at="{ item }">
        <span v-if="item.created_at" class="text-no-wrap" :title="formatDateTime(item.created_at)">{{ timeAgo(item.created_at) }}</span>
      </template>
      <template #item.by="{ item }"><BucketWriter v-if="item.kind === 'file'" :by="item.by" :function-name="functionName" /></template>
      <template #item.state="{ item }"><FileState v-if="item.kind === 'file'" :state="item.state" /></template>
      <template #item.tools="{ item }">
        <div class="d-flex justify-end" @click.stop>
          <template v-if="item.kind === 'file'">
            <v-btn icon="mdi-eye-outline" size="small" variant="text" :disabled="item.latest_complete == null"
                   :aria-label="`Preview ${baseName(item.name)}`" @click="preview(item)" />
            <v-btn icon="mdi-download" size="small" variant="text" :disabled="item.latest_complete == null"
                   :aria-label="`Download ${baseName(item.name)}`" @click="downloadOne(item)" />
          </template>
          <v-menu location="bottom end">
            <template #activator="{ props: menu }">
              <v-btn v-bind="menu" icon="mdi-dots-vertical" size="small" variant="text" :aria-label="`More for ${baseName(item.name)}`" />
            </template>
            <v-list density="compact" rounded="lg" min-width="200">
              <template v-if="item.kind === 'file'">
                <v-list-item prepend-icon="mdi-history" :title="`Versions (${item.versions})`" @click="versionsOf = item.name" />
                <v-list-item v-if="can('writer')" prepend-icon="mdi-delete-outline" title="Delete" base-color="error" @click="removeFile(item)" />
              </template>
              <template v-else>
                <v-list-item prepend-icon="mdi-folder-open-outline" title="Open" @click="open(item)" />
                <v-list-item prepend-icon="mdi-folder-download-outline" title="Download as .zip" @click="downloadFolder(item)" />
              </template>
            </v-list>
          </v-menu>
        </div>
      </template>
    </AxoDataTable>

    <div v-if="next" class="d-flex justify-center mt-3">
      <AxoBtn look="plain" :loading="loadingMore" @click="loadMore">Load more</AxoBtn>
    </div>

    <div v-if="dragging && can('writer')" class="axo-drop-overlay">
      <v-icon size="48" color="primary">mdi-cloud-upload-outline</v-icon>
      <div class="text-h6 mt-2">Drop to upload to {{ folder ? baseName(folder) : name }}</div>
      <div class="text-body-2 axo-muted">Files and folders; a file that exists becomes its next version.</div>
    </div>

    <BucketVersionsDialog :bucket="name" :file="versionsOf" :can-write="can('writer')" :function-name="functionName"
                          @close="versionsOf = null" @changed="refresh" />
    <AxoFilePreview v-model="previewing" v-model:index="previewIndex" :files="previewFiles" />
  </div>
</template>

<script setup lang="ts">
import type { FileEntry } from '../../../../utils/buckets'
import type { PreviewFile } from 'axo-ui-kit/app/components/AxoFilePreview.vue'

// One folder of the bucket: folders first, then each file's newest version,
// who wrote it and how many versions it has; 200 at a time ("Load more").
// Writers upload by dropping files / folders here or with Upload (in pieces,
// see useBucketUploads; their progress is in the uploads panel, bottom
// right); several files or folders download as one zip.
const { name, bucket, can, call, reload: reloadBucket } = useCurrentBucket()
const route = useRoute()
const notify = useNotifier()
const confirmDialog = useConfirm()
const files = useBucketFiles()
const uploader = useBucketUploads()
const ves = useVesStore()
const { apiFetch } = useApi()

const folder = computed(() => String(route.query.path ?? ''))
const crumbs = computed(() => {
  const parts = folder.value.split('/').filter(Boolean)
  return [{ title: name.value.split('/')[1] ?? name.value, path: '' },
    ...parts.map((p, i) => ({ title: p, path: `${parts.slice(0, i + 1).join('/')}/` }))]
})

const headers = [
  { title: 'Name', key: 'name', sortable: false },
  { title: 'Size', key: 'size', align: 'end' as const, sortable: false },
  { title: 'Versions', key: 'versions', align: 'end' as const, sortable: false },
  { title: 'Last written', key: 'created_at', sortable: false },
  { title: 'By', key: 'by', sortable: false },
  { title: 'State', key: 'state', sortable: false },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]

// ── listing ────────────────────────────────────────────────────────────────
const entries = ref<FileEntry[]>([])
const next = ref<string | null>(null)
const loading = ref(true)
const loadingMore = ref(false)
const error = ref<unknown>(null)
const search = ref('')
const selected = ref<string[]>([])
const prefix = computed(() => folder.value + (search.value ?? '').replace(/^\/+/, ''))

async function page(after: string | null) {
  return call<{ entries: FileEntry[]; next: string | null }>('/objects', {
    query: { prefix: prefix.value, ...(after ? { after } : {}), limit: 200 },
  })
}
async function reload() {
  error.value = null
  loading.value = true
  try {
    const answer = await page(null)
    entries.value = answer.entries
    next.value = answer.next
    selected.value = selected.value.filter(n => entries.value.some(e => e.name === n))
  } catch (err) { error.value = err } finally { loading.value = false }
}
async function loadMore() {
  loadingMore.value = true
  try {
    const answer = await page(next.value)
    entries.value.push(...answer.entries)
    next.value = answer.next
  } catch (err) { notify.error(err, "More files couldn't load") } finally { loadingMore.value = false }
}
/** After a change: the list again, and the bucket's figures (header). */
function refresh() {
  reload()
  reloadBucket().catch(() => {})
}
let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(search, () => { clearTimeout(searchTimer); searchTimer = setTimeout(reload, 300) })
watch([name, folder], () => { search.value = ''; selected.value = []; reload() })
onMounted(() => { reload(); if (!ves.loaded) ves.load().catch(() => {}) })

function open(item: FileEntry) {
  if (item.kind === 'folder') navigateTo(bucketLink(name.value, '', item.name))
  else versionsOf.value = item.name
}
const versionsOf = ref<string | null>(null)

const iconOf = (e: FileEntry) => (e.kind === 'folder' ? 'mdi-folder' : fileIcon(e.name))

// Function names for "By" (from the VEs you have a role in, while they run:
// asking a sleeping VE would wake it).
const fnNames = reactive<Record<string, Record<string, string>>>({})
const asked = new Set<string>()
watch([entries, () => ves.list], ([list]) => {
  for (const e of list) {
    const ve = e.by?.kind === 'job' ? e.by.ve_id : null
    const card = ve ? ves.byId(ve) : undefined
    if (!ve || asked.has(ve) || !card || !['Running', 'Scaling'].includes(card.lifecycle)) continue
    asked.add(ve)
    apiFetch<Array<{ function_id: string; name: string }>>(`/ves/${ve}/api/functions`)
      .then((fns) => { fnNames[ve] = Object.fromEntries(fns.map(f => [f.function_id, f.name])) })
      .catch(() => {})
  }
})
const functionName = (ve: string, fid: string) => fnNames[ve]?.[fid]

// ── preview / download ─────────────────────────────────────────────────────
const completeFiles = computed(() => entries.value.filter(e => e.kind === 'file' && e.latest_complete != null))
const refOf = (e: FileEntry) => ({ bucket: name.value, name: e.name, version: e.latest_complete, size: e.size ?? undefined })
const previewing = ref(false)
const previewIndex = ref(0)
const previewFiles = computed<PreviewFile[]>(() => files.previewOf(completeFiles.value.map(refOf)))
function preview(e: FileEntry) {
  previewIndex.value = Math.max(0, completeFiles.value.indexOf(e))
  previewing.value = true
}
const downloadOne = (e: FileEntry) => files.download(refOf(e))

const downloading = ref(false)
const selectedEntries = computed(() => entries.value.filter(e => selected.value.includes(e.name)))
const selectedText = computed(() => {
  const f = selectedEntries.value.filter(e => e.kind === 'file').length
  const d = selectedEntries.value.length - f
  if (f === 1 && !d) return ''
  return [f && `${f} file${f === 1 ? '' : 's'}`, d && `${d} folder${d === 1 ? '' : 's'}`].filter(Boolean).join(' + ') + ' (.zip)'
})
async function downloadSelected() {
  const chosen = selectedEntries.value
  await zip(chosen.filter(e => e.kind === 'file' && e.latest_complete != null), chosen.filter(e => e.kind === 'folder').map(e => e.name))
}
const downloadFolder = (e: FileEntry) => zip([], [e.name])
async function zip(fileEntries: FileEntry[], folders: string[]) {
  downloading.value = true
  try {
    const answer = await files.link(name.value, fileEntries.map(e => ({ name: e.name, version: e.latest_complete })), folders, folder.value)
    notify.info(answer.zip ? `Downloading ${answer.filename}: ${answer.files} files, ${formatBytes(answer.size)} before zipping`
      : `Downloading ${answer.filename}`)
  } catch (err) { notify.error(err, "The download couldn't start") } finally { downloading.value = false }
}

// ── delete ─────────────────────────────────────────────────────────────────
async function removeFile(e: FileEntry) {
  const file = baseName(e.name)
  if (!await confirmDialog.confirm({
    title: `Delete ${file}?`, danger: true, confirmText: 'Delete', typeToConfirm: file,
    text: e.versions === 1 ? "Its only version is removed. This can't be undone."
      : `All ${e.versions} versions are removed. To delete only one, use Versions. This can't be undone.`,
    consequences: ['Functions and choreographies that read it get "not found" from then on.'],
  })) return
  try {
    await call(`/objects/${encodePath(e.name)}`, { method: 'DELETE' })
    entries.value = entries.value.filter(x => x !== e)
    notify.success(`${file} deleted`)
    reloadBucket().catch(() => {})
  } catch (err) { notify.error(err, `${file} couldn't be deleted`) }
}

// ── uploads ────────────────────────────────────────────────────────────────
const pickFiles = ref<HTMLInputElement>()
const pickFolder = ref<HTMLInputElement>()
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let stop: (() => void) | undefined
onMounted(() => {
  stop = uploader.onEnd((u) => {
    if (u.bucket !== name.value) return
    clearTimeout(refreshTimer)
    refreshTimer = setTimeout(refresh, 400)
  })
})
onBeforeUnmount(() => stop?.())

function onPicked(ev: Event) {
  const input = ev.target as HTMLInputElement
  const list = Array.from(input.files ?? [])
  input.value = ''
  queue(list.map(f => ({ file: f, path: (f as any).webkitRelativePath || f.name })))
}

async function queue(picked: { file: File; path: string }[]) {
  if (!picked.length || !can('writer')) return
  const items = picked.map(p => ({ file: p.file, name: folder.value + p.path }))
  const MAX = 5 * 1024 ** 3
  const big = items.filter(i => i.file.size > MAX)
  if (big.length) {
    notify.warning(`${big.map(i => baseName(i.name)).join(', ')}: over 5 GB, too big to upload from the page. A job can write it (dataio.write_chunks).`)
    return
  }
  const total = items.reduce((s, i) => s + i.file.size, 0)
  const b = bucket.value!
  const room = b.quota_bytes - (b.used_bytes ?? 0)
  if (total > room) {
    notify.warning(`These files need ${formatBytes(total)} but the bucket has ${formatBytes(Math.max(0, room))} free of its ${formatBytes(b.quota_bytes)} limit. ${can('owner') ? 'Raise the limit in Settings, or' : 'Ask its owner for more room, or'} delete files first.`)
    return
  }
  const existing = new Map(entries.value.filter(e => e.kind === 'file').map(e => [e.name, e]))
  const again = items.filter(i => existing.has(i.name))
  if (again.length && !await confirmDialog.confirm({
    title: again.length === 1 ? `${baseName(again[0]!.name)} exists` : `${again.length} files exist`,
    confirmText: 'Upload as new versions',
    text: again.length === 1
      ? `It becomes version ${(existing.get(again[0]!.name)!.version ?? 0) + 1}; the older versions are kept.`
      : 'Each becomes its next version; the older versions are kept.',
    consequences: again.length > 1 ? again.slice(0, 5).map(i => `${baseName(i.name)} → v${(existing.get(i.name)!.version ?? 0) + 1}`) : undefined,
  })) return
  uploader.add(name.value, items)
}

// Dropping files or whole folders (folders keep their paths inside this one).
const dragging = ref(false)
let depth = 0
function onDragEnter(ev: DragEvent) {
  if (!ev.dataTransfer?.types.includes('Files')) return
  depth++
  dragging.value = true
}
function onDragLeave() {
  depth = Math.max(0, depth - 1)
  if (!depth) dragging.value = false
}
async function onDrop(ev: DragEvent) {
  depth = 0
  dragging.value = false
  const items = Array.from(ev.dataTransfer?.items ?? [])
  const entriesIn = items.map(i => (i as any).webkitGetAsEntry?.()).filter(Boolean)
  if (!entriesIn.length) {
    queue(Array.from(ev.dataTransfer?.files ?? []).map(f => ({ file: f, path: f.name })))
    return
  }
  const out: { file: File; path: string }[] = []
  await Promise.all(entriesIn.map((e: any) => walk(e, '', out)))
  queue(out)
}
async function walk(entry: any, base: string, out: { file: File; path: string }[]): Promise<void> {
  if (entry.isFile) {
    const file: File = await new Promise((ok, fail) => entry.file(ok, fail))
    out.push({ file, path: base + file.name })
    return
  }
  const reader = entry.createReader()
  for (;;) {
    const batch: any[] = await new Promise((ok, fail) => reader.readEntries(ok, fail))
    if (!batch.length) return
    await Promise.all(batch.map(child => walk(child, `${base}${entry.name}/`, out)))
  }
}
</script>

<style scoped>
.axo-drop-area { position: relative; min-height: 300px; }
.axo-drop-overlay {
  position: absolute; inset: 0; z-index: 5; display: flex; flex-direction: column; align-items: center; justify-content: center;
  border: 2px dashed rgb(var(--v-theme-primary)); border-radius: 12px;
  background: rgba(var(--v-theme-surface), 0.92); pointer-events: none;
}
</style>
