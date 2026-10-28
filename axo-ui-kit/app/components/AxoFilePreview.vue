<template>
  <v-dialog :model-value="modelValue" fullscreen transition="fade-transition" scrim="black"
            @update:model-value="emit('update:modelValue', $event)" @keydown="onKey">
    <div class="axo-preview">
      <header class="axo-preview-bar">
        <v-btn icon="mdi-close" variant="text" aria-label="Close" @click="emit('update:modelValue', false)" />
        <v-icon :icon="icon" class="mx-1" />
        <div class="axo-preview-title">
          <div class="text-body-1 text-truncate">{{ file?.name }}</div>
          <div class="text-caption text-truncate axo-preview-sub">
            {{ file?.subtitle }}<template v-if="file?.size != null"> · {{ formatBytes(file.size) }}</template>
          </div>
        </div>
        <v-spacer />
        <span v-if="files.length > 1" class="text-caption mr-2 axo-preview-sub">{{ index + 1 }} of {{ files.length }}</span>
        <v-btn icon="mdi-download" variant="text" aria-label="Download" @click="file?.download()" />
      </header>

      <div class="axo-preview-stage">
        <v-btn v-if="files.length > 1" class="axo-preview-nav axo-preview-nav--left" icon="mdi-chevron-left" variant="tonal"
               aria-label="Previous file" :disabled="index === 0" @click="go(-1)" />
        <div class="axo-preview-content">
          <v-progress-circular v-if="loading" indeterminate color="white" />
          <div v-else-if="problem" class="axo-preview-none">
            <v-icon size="56" class="mb-3">mdi-file-alert-outline</v-icon>
            <div class="text-body-1 mb-1">{{ problem }}</div>
            <AxoBtn prepend-icon="mdi-download" class="mt-3" @click="file?.download()">Download</AxoBtn>
          </div>
          <template v-else>
            <img v-if="kind === 'image'" :src="url!" :alt="file?.name" class="axo-preview-media">
            <iframe v-else-if="kind === 'pdf'" :src="url!" :title="file?.name" class="axo-preview-frame" />
            <video v-else-if="kind === 'video'" :src="url!" controls class="axo-preview-media" />
            <audio v-else-if="kind === 'audio'" :src="url!" controls />
            <div v-else-if="kind === 'csv'" class="axo-preview-sheet">
              <v-table density="compact" fixed-header height="100%">
                <thead><tr><th v-for="(h, i) in table[0] ?? []" :key="i">{{ h }}</th></tr></thead>
                <tbody><tr v-for="(r, i) in table.slice(1)" :key="i"><td v-for="(c, j) in r" :key="j">{{ c }}</td></tr></tbody>
              </v-table>
              <div v-if="cut" class="text-caption pa-2">The first {{ TABLE_ROWS }} rows are shown; download it for the rest.</div>
            </div>
            <div v-else-if="kind === 'json' || kind === 'text'" class="axo-preview-sheet axo-preview-sheet--text">
              <AxoCode :model-value="text" :language="kind === 'json' ? 'json' : 'text'" readonly max-height="100%" />
              <div v-if="cut" class="text-caption pa-2">The start of the file is shown; download it for the rest.</div>
            </div>
          </template>
        </div>
        <v-btn v-if="files.length > 1" class="axo-preview-nav axo-preview-nav--right" icon="mdi-chevron-right" variant="tonal"
               aria-label="Next file" :disabled="index >= files.length - 1" @click="go(1)" />
      </div>
    </div>
  </v-dialog>
</template>

<script setup lang="ts">
export interface PreviewFile {
  key: string
  name: string
  subtitle?: string
  size?: number | null
  /** The bytes (a read: checked and billed like a download). */
  load: () => Promise<Blob>
  download: () => unknown
}

// A full-page viewer of files (like Google Drive's): images, PDFs, video,
// audio, text, CSV (as a table) and JSON, whatever the browser can show;
// anything else says so and offers Download. Previous / next (← →), Esc
// closes. Files above PREVIEW_MAX are only downloaded.
const props = defineProps<{ modelValue: boolean; files: PreviewFile[]; index: number }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'update:index', v: number): void }>()

const PREVIEW_MAX = 25 * 1024 * 1024
const TEXT_MAX = 512 * 1024
const TABLE_ROWS = 500
const KINDS: Record<string, string[]> = {
  image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'ico'],
  pdf: ['pdf'],
  video: ['mp4', 'webm', 'ogv', 'mov', 'm4v'],
  audio: ['mp3', 'wav', 'ogg', 'oga', 'm4a', 'flac', 'aac'],
  csv: ['csv', 'tsv'],
  json: ['json', 'geojson', 'ipynb'],
  text: ['txt', 'md', 'log', 'py', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'xml', 'html', 'js', 'ts', 'sql', 'sh', 'r', 'env'],
}
const TYPES: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml',
  bmp: 'image/bmp', avif: 'image/avif', ico: 'image/x-icon', pdf: 'application/pdf', mp4: 'video/mp4', webm: 'video/webm',
  ogv: 'video/ogg', mov: 'video/quicktime', m4v: 'video/mp4', mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg',
  oga: 'audio/ogg', m4a: 'audio/mp4', flac: 'audio/flac', aac: 'audio/aac',
}
const ICONS: Record<string, string> = {
  image: 'mdi-file-image-outline', pdf: 'mdi-file-pdf-box', video: 'mdi-file-video-outline', audio: 'mdi-file-music-outline',
  csv: 'mdi-file-table-outline', json: 'mdi-code-json', text: 'mdi-file-document-outline', none: 'mdi-file-outline',
}

const file = computed(() => props.files[props.index])
const ext = computed(() => (file.value?.name.split('.').pop() ?? '').toLowerCase())
const kind = computed(() => Object.entries(KINDS).find(([, list]) => list.includes(ext.value))?.[0] ?? 'none')
const icon = computed(() => ICONS[kind.value])

const loading = ref(false)
const problem = ref<string | null>(null)
const url = ref<string | null>(null)
const text = ref('')
const table = ref<string[][]>([])
const cut = ref(false)
let generation = 0

function release() {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = null
}

async function show() {
  const f = file.value
  const mine = ++generation
  release()
  problem.value = null
  text.value = ''
  table.value = []
  cut.value = false
  if (!props.modelValue || !f) return
  if (kind.value === 'none') { problem.value = 'This kind of file has no preview.'; return }
  if (f.size != null && f.size > PREVIEW_MAX) { problem.value = 'This file is too large to preview.'; return }
  loading.value = true
  try {
    const blob = await f.load()
    if (mine !== generation) return
    if (['csv', 'json', 'text'].includes(kind.value)) {
      cut.value = blob.size > TEXT_MAX
      const raw = await blob.slice(0, TEXT_MAX).text()
      if (kind.value === 'csv') {
        const sep = ext.value === 'tsv' ? '\t' : ','
        const lines = raw.split(/\r?\n/).filter(Boolean)
        cut.value ||= lines.length > TABLE_ROWS + 1
        table.value = lines.slice(0, TABLE_ROWS + 1).map(l => splitCsv(l, sep))
      } else if (kind.value === 'json' && !cut.value) {
        try { text.value = JSON.stringify(JSON.parse(raw), null, 2) } catch { text.value = raw }
      } else {
        text.value = raw
      }
    } else {
      url.value = URL.createObjectURL(new Blob([blob], { type: TYPES[ext.value] ?? blob.type }))
    }
  } catch (err) {
    if (mine === generation) problem.value = `It couldn't be opened: ${(err as Error)?.message ?? 'unknown error'}`
  } finally {
    if (mine === generation) loading.value = false
  }
}

/** One CSV line, quotes respected. */
function splitCsv(line: string, sep: string): string[] {
  const out: string[] = []
  let cur = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!
    if (ch === '"') { if (quoted && line[i + 1] === '"') { cur += '"'; i++ } else quoted = !quoted }
    else if (ch === sep && !quoted) { out.push(cur); cur = '' }
    else cur += ch
  }
  out.push(cur)
  return out
}

function go(step: number) {
  const next = props.index + step
  if (next >= 0 && next < props.files.length) emit('update:index', next)
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowLeft') go(-1)
  else if (e.key === 'ArrowRight') go(1)
}

watch(() => [props.modelValue, file.value?.key], show, { immediate: true })
onBeforeUnmount(release)
</script>

<style scoped>
.axo-preview { display: flex; flex-direction: column; height: 100%; background: rgba(15, 17, 21, 0.96); color: #fff; }
.axo-preview-bar { display: flex; align-items: center; gap: 4px; padding: 6px 12px; min-height: 56px; }
.axo-preview-title { min-width: 0; }
.axo-preview-sub { opacity: 0.7; }
.axo-preview-stage { flex: 1; position: relative; display: flex; align-items: center; justify-content: center; min-height: 0; padding: 0 64px 24px; }
.axo-preview-content { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; min-height: 0; }
.axo-preview-media { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 4px; }
.axo-preview-frame { width: 100%; height: 100%; border: 0; background: #fff; border-radius: 4px; }
.axo-preview-sheet { width: min(1100px, 100%); height: 100%; overflow: auto; background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface)); border-radius: 8px; }
.axo-preview-sheet--text { padding: 8px; }
.axo-preview-none { text-align: center; }
.axo-preview-nav { position: absolute; top: 50%; transform: translateY(-50%); color: #fff; }
.axo-preview-nav--left { left: 12px; }
.axo-preview-nav--right { right: 12px; }
@media (max-width: 600px) {
  .axo-preview-stage { padding: 0 8px 64px; }
  .axo-preview-nav { top: auto; bottom: 8px; transform: none; }
}
</style>
