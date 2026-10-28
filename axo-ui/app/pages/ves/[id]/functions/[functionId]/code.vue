<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-3">
      <v-select v-model="version" :items="choices" label="Version" density="compact" variant="outlined" hide-details class="axo-pick" />
      <v-switch v-model="comparing" color="primary" inset hide-details density="compact" label="Compare" class="flex-grow-0"
                :disabled="versions.length < 2" />
      <v-select v-if="comparing" v-model="other" :items="choices" label="With" density="compact" variant="outlined" hide-details class="axo-pick" />
      <v-spacer />
      <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-content-copy" :disabled="!code?.text" @click="copy">Copy</v-btn>
      <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-download" :disabled="!code?.downloadable"
             :title="code && !code.downloadable ? 'A pickle runs; this text only shows it' : undefined" @click="download">Download .py</v-btn>
    </div>

    <v-alert v-if="code && code.code_format !== 'source'" type="info" variant="tonal" density="compact" class="mb-3">
      This version was sent by the SDK as a pickle (its code couldn't stand alone). The text below is what it was made from: it shows
      the code, but what runs is the pickle, so it can't be downloaded.
    </v-alert>
    <AxoErrorAlert v-if="error" :error="error" title="The code couldn't load" :retry="load" class="mb-3" />
    <AxoSkeleton v-else-if="loading && !code" :lines="12" />
    <template v-else-if="code">
      <AxoCodeCompare v-if="comparing && otherCode" :left="otherCode.text ?? ''" :right="code.text ?? ''"
                      :left-title="`v${otherCode.version}`" :right-title="`v${code.version}`" />
      <AxoCode v-else-if="code.text != null" :model-value="code.text" language="python" readonly min-height="200px" max-height="70vh"
               :label="`${code.name} v${code.version}`" />
      <AxoEmptyState v-else icon="mdi-code-tags" title="No readable code" text="The SDK sent this version without its text." />
      <div class="text-caption axo-muted mt-2">
        v{{ code.version }} · registered by {{ code.registered_by_name ?? '—' }}
        <template v-if="code.registered_at"> · {{ formatDateTime(ts(code.registered_at)) }}</template>
        · hash <span class="axo-mono">{{ code.code_hash?.slice(0, 12) }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { FunctionCode } from '~/utils/functions'

// A version's code, read-only (everyone with access to the VE), and two
// versions side by side. `?version=` and `?with=` are in the URL.
const { api } = useCurrentVe()
const { functionId, versions, row } = useCurrentFunction()
const route = useRoute()
const router = useRouter()

const choices = computed(() => versions.value.map(v => ({ value: v.version, title: `v${v.version}${v.deleted_at ? ' (deleted)' : ''}` })))
const version = ref<number>(Number(route.query.version) || row.value?.latest.version || versions.value[0]?.version || 1)
const other = ref<number>(Number(route.query.with) || versions.value.find(v => v.version < version.value)?.version || version.value)
const comparing = ref(!!route.query.with)
watch([version, other, comparing], () => router.replace({ query: { ...route.query, version: String(version.value),
  with: comparing.value ? String(other.value) : undefined } }))

const cache = new Map<number, FunctionCode>()
const code = ref<FunctionCode | null>(null)
const otherCode = ref<FunctionCode | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
async function fetchCode(v: number) {
  if (!cache.has(v)) cache.set(v, await api<FunctionCode>(`/functions/${functionId.value}/${v}/code`))
  return cache.get(v)!
}
async function load() {
  loading.value = true
  error.value = null
  try {
    code.value = await fetchCode(version.value)
    otherCode.value = comparing.value ? await fetchCode(other.value) : null
  } catch (err) { error.value = err } finally { loading.value = false }
}
watch([version, other, comparing, functionId], load)
onMounted(load)

async function copy() {
  try { await navigator.clipboard.writeText(code.value?.text ?? ''); useNotifier().success('Copied') } catch { useNotifier().warning("Couldn't copy") }
}
function download() {
  if (!code.value?.text) return
  saveBlob(new Blob([code.value.text], { type: 'text/x-python' }), `${code.value.name}_v${code.value.version}.py`)
}
</script>

<style scoped>
.axo-pick { flex: 0 1 180px; min-width: 140px; }
</style>
