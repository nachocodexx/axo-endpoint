<template>
  <div>
    <template v-if="doc">
    <v-row>
      <v-col cols="12" md="4" lg="3">
        <v-list density="compact" class="axo-versions" nav>
          <v-list-item v-for="v in sorted" :key="v.version" :active="v.version === picked" color="primary" @click="pick(v.version)">
            <v-list-item-title>v{{ v.version }}<span v-if="v.version === doc.latest_version" class="text-caption axo-muted"> · current</span></v-list-item-title>
            <v-list-item-subtitle>{{ timeAgo(ts(v.updated_at)) }}{{ v.name !== doc.name ? ` · named ${v.name}` : '' }}</v-list-item-subtitle>
            <template #append>
              <v-checkbox-btn :model-value="compareWith === v.version" :disabled="v.version === picked" density="compact"
                              :aria-label="`Compare with v${v.version}`" @click.stop="compareWith = compareWith === v.version ? null : v.version" />
            </template>
          </v-list-item>
        </v-list>
        <div class="text-caption axo-muted px-2">Tick a second version to compare.</div>
      </v-col>
      <v-col cols="12" md="8" lg="9">
        <div class="d-flex align-center flex-wrap ga-2 mb-2">
          <span class="text-subtitle-1 font-weight-medium">v{{ picked }}</span>
          <template v-if="compareWith">
            <span class="axo-muted">compared with v{{ compareWith }}</span>
            <v-chip v-if="diff" size="small" variant="tonal" color="success">{{ diff.added.length }} added</v-chip>
            <v-chip v-if="diff" size="small" variant="tonal" color="warning">{{ diff.changed.length }} changed</v-chip>
            <v-chip v-if="diff" size="small" variant="tonal" color="error">{{ diff.removed.length }} removed</v-chip>
          </template>
          <v-spacer />
          <template v-if="can('developer')">
            <v-btn variant="text" class="text-none" prepend-icon="mdi-play" @click="runOpen = true">Run this version</v-btn>
            <v-btn v-if="picked !== doc.latest_version" variant="tonal" class="text-none" prepend-icon="mdi-pencil-outline"
                   :to="`/ves/${id}/choreographies/${encodeURIComponent(name)}/designer?from=${picked}`">Edit from this version</v-btn>
          </template>
        </div>
        <AxoSkeleton v-if="!current" :lines="6" />
        <div v-else-if="compareWith && other" class="axo-versions__compare">
          <div>
            <div class="text-caption axo-muted mb-1">v{{ compareWith }}</div>
            <ChoreographyCanvas :graph="layout(other.graph)" :canvas-id="`ch-v-${compareWith}-l`" :fname="nameOf" height="440px" :minimap="false" />
          </div>
          <div>
            <div class="text-caption axo-muted mb-1">v{{ picked }} <span v-if="diff">· green = added, amber = changed</span></div>
            <ChoreographyCanvas :graph="layout(current.graph)" :canvas-id="`ch-v-${picked}-r`" :fname="nameOf" :diff="diff" height="440px" :minimap="false" />
          </div>
        </div>
        <ChoreographyCanvas v-else :graph="layout(current.graph)" :canvas-id="`ch-v-${picked}`" :fname="nameOf" height="480px" />
        <v-list v-if="diff && (diff.removed.length || diff.edgesRemoved.length || diff.edgesAdded.length || diff.triggers.length || diff.calls.length)" density="compact" class="mt-2">
          <v-list-item v-for="r in diff.removed" :key="`r${r}`" prepend-icon="mdi-minus-circle-outline" base-color="error" :title="`Step ${r} removed`" />
          <v-list-item v-for="e in diff.edgesAdded" :key="`ea${e}`" prepend-icon="mdi-arrow-right" base-color="success" :title="`Arrow ${e} added`" />
          <v-list-item v-for="e in diff.edgesRemoved" :key="`er${e}`" prepend-icon="mdi-arrow-right" base-color="error" :title="`Arrow ${e} removed`" />
          <v-list-item v-for="c in diff.calls" :key="`c${c}`" prepend-icon="mdi-layers-triple-outline" base-color="warning" :title="c" />
          <v-list-item v-for="t in diff.triggers" :key="`t${t}`" prepend-icon="mdi-lightning-bolt" :title="`Trigger ${t}`" />
        </v-list>
      </v-col>
    </v-row>
    <ChoreographyRunDialog v-model="runOpen" :doc="doc" :versions="versions" :initial-version="picked" />
    </template>
  </div>
</template>

<script setup lang="ts">
// Every saved version: view one's graph, compare two side by side (added
// steps green, changed amber, the rest listed), edit from an older one (it
// becomes the designer's draft) or run it.
import type { ChGraph, Choreography } from '~/utils/choreographies'

const { id, can } = useCurrentVe()
const { name, doc, versions, version } = useCurrentChoreography()
const { nameOf, load: loadFunctions } = useFunctions()
const route = useRoute()
const router = useRouter()
const sorted = computed(() => [...versions.value].sort((a, b) => b.version - a.version))
const picked = ref<number>(Number(route.query.v) || 0)
const compareWith = ref<number | null>(route.query.with ? Number(route.query.with) : null)
const cache = reactive<Record<number, Choreography>>({})
const runOpen = ref(false)
async function get(v: number) { if (!cache[v]) cache[v] = await version(v) }
const current = computed(() => cache[picked.value] ?? null)
const other = computed(() => (compareWith.value ? cache[compareWith.value] ?? null : null))
const diff = computed(() => (current.value && other.value ? diffGraphs(other.value.graph, current.value.graph) : null))
const layout = (g: ChGraph) => { const x = { ...g, triggers: g.triggers ?? [] }; return needsLayout(x) ? arrange(x) : x }
function pick(v: number) { picked.value = v; if (compareWith.value === v) compareWith.value = null }
watch(() => doc.value?.latest_version, (v) => { if (v && !picked.value) picked.value = v }, { immediate: true })
watch([picked, compareWith], async () => {
  router.replace({ query: { ...route.query, v: picked.value || undefined, with: compareWith.value ?? undefined } })
  if (picked.value) await get(picked.value)
  if (compareWith.value) await get(compareWith.value)
}, { immediate: true })
onMounted(loadFunctions)
</script>

<style scoped>
.axo-versions { max-height: 560px; overflow-y: auto; border-radius: 10px; border: 1px solid rgba(var(--v-border-color), 0.12); }
.axo-versions__compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 960px) { .axo-versions__compare { grid-template-columns: 1fr; } }
</style>
