<template>
  <div>
    <v-breadcrumbs :items="[{ title: 'Objects', to: `/ves/${id}/objects` }, { title: 'Classes', to: `/ves/${id}/objects?tab=classes` }, { title: className, disabled: true }]"
                   density="compact" class="px-0 pt-0 mb-1" />
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: the class shows here once it runs.
    </v-alert>
    <AxoErrorAlert v-else-if="store.error.value" :error="store.error.value" title="The class couldn't load" :retry="() => store.load(true)" class="mb-4" />
    <AxoEmptyState v-else-if="loaded && !row" icon="mdi-code-braces" title="This class is not in the VE" text="Its versions may have been deleted." />
    <AxoSkeleton v-else-if="!row" :lines="8" />

    <template v-if="row">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <v-icon color="primary">mdi-code-braces</v-icon>
        <span class="text-h6 axo-mono text-truncate">{{ row.class_name }}</span>
        <span class="text-body-2 axo-muted">v{{ row.latest.version }}</span>
        <v-spacer />
        <template v-if="can('developer')">
          <v-btn variant="tonal" class="text-none" prepend-icon="mdi-source-branch-plus"
                 :to="`/ves/${id}/objects/classes/new?class=${encodeURIComponent(row.class_name)}`">New version</v-btn>
          <AxoBtn prepend-icon="mdi-shape-plus-outline" @click="newOpen = true">New object</AxoBtn>
        </template>
      </div>
      <div class="text-body-2 axo-muted mb-3">{{ row.latest.description || 'No description (the first line of its docstring shows here).' }}</div>

      <v-tabs v-model="tab" color="primary" class="mb-5 axo-tabs">
        <v-tab value="overview" prepend-icon="mdi-view-grid-outline">Overview</v-tab>
        <v-tab value="versions" prepend-icon="mdi-source-branch">Versions</v-tab>
        <v-tab value="code" prepend-icon="mdi-code-tags">Code</v-tab>
      </v-tabs>

      <!-- ── Overview ── -->
      <v-row v-if="tab === 'overview'" density="compact">
        <v-col cols="12" lg="7">
          <AxoPanel :title="`Methods (v${row.latest.version})`" icon="mdi-function" stacked flush
                    help="What people can call: run and every method marked @axo_method. Parameters come from the code (type hints, defaults, the docstring's Args).">
            <template v-for="(m, i) in [...(row.latest.init ? [{ ...row.latest.init, name: '__init__' }] : []), ...row.latest.methods]" :key="m.name">
              <v-divider v-if="i" />
              <div class="px-4 py-3">
                <div class="d-flex align-center ga-2">
                  <span class="axo-mono font-weight-medium">{{ m.name }}({{ m.params.join(', ') }})</span>
                  <v-chip v-if="m.name === '__init__'" size="x-small" variant="tonal">runs when an object is made</v-chip>
                </div>
                <div v-if="m.doc" class="text-body-2 axo-muted mt-1">{{ m.doc }}</div>
                <div v-for="p in methodFields(m)" :key="p.name" class="text-caption mt-1">
                  <span class="axo-mono">{{ p.name }}</span>
                  <span class="axo-muted"> · {{ p.type }}{{ p.required ? ' · required' : p.default !== undefined && p.default !== null ? ` · default ${JSON.stringify(p.default)}` : ' · optional' }}</span>
                  <span v-if="p.description" class="axo-muted"> — {{ p.description }}</span>
                </div>
              </div>
            </template>
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="5">
          <AxoPanel title="Upgrading objects" icon="mdi-arrow-up-bold-circle-outline" stacked class="mb-3">
            <div class="text-body-2">
              <template v-if="row.latest.has_upgrade">v{{ row.latest.version }} has an <code>upgrade(self, old_version)</code> step: it runs once on each object moved to it.</template>
              <template v-else>v{{ row.latest.version }} has no upgrade step: an object moved to it keeps its values as they are.</template>
            </div>
            <template v-if="older.length">
              <div class="text-body-2 mt-3">{{ older.length }} object{{ older.length === 1 ? ' is' : 's are' }} on older versions.</div>
              <AxoBtn v-if="can('developer')" class="mt-2" prepend-icon="mdi-arrow-up-bold-circle-outline" :loading="upgradingAll" @click="upgradeAll">
                Upgrade all {{ older.length }} to v{{ row.latest.version }}
              </AxoBtn>
            </template>
          </AxoPanel>
          <AxoPanel title="Objects per version" icon="mdi-shape-outline" stacked flush>
            <v-list v-if="store.objectsOn(row.class_name).length" density="compact" class="py-0">
              <template v-for="v in row.versions" :key="v.version">
                <v-list-subheader v-if="store.objectsOn(row.class_name, v.version).length">v{{ v.version }}</v-list-subheader>
                <v-list-item v-for="o in store.objectsOn(row.class_name, v.version)" :key="o.name"
                             :to="`/ves/${id}/objects/${encodeURIComponent(o.name)}`" :title="o.name" class="axo-mono"
                             :subtitle="`revision ${o.revision} · ${formatBytes(o.size_bytes)}`" />
              </template>
            </v-list>
            <div v-else class="pa-4 text-body-2 axo-muted">No objects yet.</div>
          </AxoPanel>
        </v-col>
      </v-row>

      <!-- ── Versions ── -->
      <AxoDataTable v-else-if="tab === 'versions'" :headers="versionHeaders" :items="row.versions" item-value="version" :searchable="false"
                    :sort-by="[{ key: 'version', order: 'desc' }]" empty-icon="mdi-source-branch" empty-title="No versions">
        <template #item.version="{ item }">
          <span class="font-weight-medium">v{{ item.version }}</span>
          <v-chip v-if="item.version === row.latest.version" size="x-small" variant="tonal" color="primary" class="ml-2">newest</v-chip>
        </template>
        <template #item.methods="{ item }"><span class="axo-mono text-body-2">{{ methodNames(item) }}</span></template>
        <template #item.upgrade="{ item }"><v-icon v-if="item.has_upgrade" size="18" color="success" aria-label="Has an upgrade step">mdi-check</v-icon></template>
        <template #item.objects="{ item }"><span class="text-body-2">{{ store.objectsOn(item.class_name, item.version).length }}</span></template>
        <template #item.by="{ item }"><span class="text-body-2">{{ item.created_by ?? '—' }}</span></template>
        <template #item.at="{ item }"><span class="text-body-2" :title="formatExactTime(ts(item.created_at))">{{ item.created_at ? timeAgo(ts(item.created_at)) : '—' }}</span></template>
        <template #item.actions="{ item }">
          <div class="d-flex justify-end ga-1">
            <v-btn size="small" variant="text" class="text-none" @click="showCode(item.version)">Code</v-btn>
            <v-btn v-if="actions.mayDeleteVersion(item)" size="small" variant="text" color="error" class="text-none"
                   :disabled="store.objectsOn(item.class_name, item.version).length > 0"
                   :title="store.objectsOn(item.class_name, item.version).length ? 'Objects are on it: upgrade or delete them first' : undefined"
                   :loading="actions.busy.value === `v:${item.class_name}:${item.version}`" @click="actions.removeVersion(item)">Delete</v-btn>
          </div>
        </template>
      </AxoDataTable>

      <!-- ── Code ── -->
      <div v-else>
        <div class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-select v-model="codeVersion" :items="versionChoices" label="Version" density="compact" variant="outlined" hide-details class="axo-pick" />
          <v-switch v-model="comparing" color="primary" inset hide-details density="compact" label="Compare" class="flex-grow-0" :disabled="row.versions.length < 2" />
          <v-select v-if="comparing" v-model="otherVersion" :items="versionChoices" label="With" density="compact" variant="outlined" hide-details class="axo-pick" />
        </div>
        <AxoErrorAlert v-if="codeError" :error="codeError" title="The code couldn't load" class="mb-3" />
        <AxoCodeCompare v-else-if="comparing && code[otherVersion] != null && code[codeVersion] != null"
                        :left="code[otherVersion]!" :right="code[codeVersion]!" :left-title="`v${otherVersion}`" :right-title="`v${codeVersion}`" />
        <AxoCode v-else-if="code[codeVersion] != null" :model-value="code[codeVersion]!" language="python" readonly min-height="300px" max-height="70vh" label="Code" />
        <AxoSkeleton v-else :lines="10" />
      </div>
    </template>

    <ObjectNewDialog v-model="newOpen" :class-name="className" />
  </div>
</template>

<script setup lang="ts">
// One class: Overview (methods with their parameters, __init__, the upgrade
// step, objects per version, "Upgrade all"), Versions (who registered each,
// delete one no object is on) and Code (any version, or two compared).
const { id, ve, can, vemUp, api } = useCurrentVe()
const store = useObjects()
const actions = useObjectActions()
const notifier = useNotifier()
const route = useRoute()
const router = useRouter()
const className = computed(() => String(route.params.className ?? ''))
const row = computed(() => store.classOf(className.value))
useHead(() => ({ title: `${className.value} · Classes · ${ve.value?.name ?? 'VE'} · axo` }))

const tab = ref(['overview', 'versions', 'code'].includes(route.query.tab as string) ? (route.query.tab as string) : 'overview')
watch(tab, t => router.replace({ query: { ...route.query, tab: t === 'overview' ? undefined : t } }))
const newOpen = ref(false)

const older = computed(() => (row.value ? store.objectsOn(row.value.class_name).filter(o => o.class_version < row.value!.latest.version) : []))
const upgradingAll = ref(false)
async function upgradeAll() {
  if (!row.value) return
  upgradingAll.value = true
  const target = row.value.latest.version
  let started = 0
  const problems: string[] = []
  for (const o of older.value) {
    try {
      await api(`/objects/${encodeURIComponent(o.name)}/upgrade`, { method: 'POST', body: { class_version: target } })
      started++
    } catch (err) { problems.push(`${o.name}: ${(err as any)?.message ?? 'refused'}`) }
  }
  upgradingAll.value = false
  if (started) notifier.success(`${started} upgrade${started === 1 ? '' : 's'} to v${target} started: each runs as a job`)
  if (problems.length) notifier.warning(`Not started: ${problems.slice(0, 3).join(' · ')}`)
  setTimeout(() => store.load(true), 4000)
}

const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const versionHeaders = [
  { title: 'Version', key: 'version', sortable: true },
  { title: 'Methods', key: 'methods', sortable: false, ...WIDE },
  { title: 'Upgrade step', key: 'upgrade', sortable: false, align: 'center' as const, ...WIDE },
  { title: 'Objects', key: 'objects', sortable: false },
  { title: 'Registered by', key: 'by', sortable: false, ...WIDE },
  { title: 'Registered', key: 'at', sortable: false },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]

// ── code ───────────────────────────────────────────────────────────────────
const versionChoices = computed(() => (row.value?.versions ?? []).map(v => ({ value: v.version, title: `v${v.version}` })))
const codeVersion = ref(0)
const otherVersion = ref(0)
const comparing = ref(false)
const code = reactive<Record<number, string | null>>({})
const codeError = ref<unknown>(null)
watch(row, (r) => {
  if (r && !codeVersion.value) {
    codeVersion.value = r.latest.version
    otherVersion.value = r.versions[1]?.version ?? r.latest.version
  }
}, { immediate: true })
async function loadCode(v: number) {
  const doc = row.value?.versions.find(x => x.version === v)
  if (!doc || code[v] !== undefined) return
  try {
    code[v] = (await api<{ text: string | null }>(`/functions/${doc.function_id}/${v}/code`)).text ?? '# The code is kept as a pickle (no readable source).'
    codeError.value = null
  } catch (err) { codeError.value = err }
}
watch([tab, codeVersion, otherVersion, comparing], () => {
  if (tab.value !== 'code') return
  loadCode(codeVersion.value)
  if (comparing.value) loadCode(otherVersion.value)
})
function showCode(v: number) { codeVersion.value = v; tab.value = 'code' }

const loaded = ref(false)
async function load() { await store.load(true); loaded.value = true }
onMounted(load)
watch([className, vemUp], load)
useVeLive(() => (vemUp.value ? 'objects' : null), () => store.load(true))
</script>

<style scoped>
.axo-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-pick { flex: 0 1 160px; }
</style>
