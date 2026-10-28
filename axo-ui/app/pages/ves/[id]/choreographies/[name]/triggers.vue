<template>
  <div>
    <template v-if="doc">
    <div class="d-flex align-center mb-3">
      <span class="text-body-2 axo-muted">What starts a run on its own. Triggers are part of each version: change them in the designer.</span>
      <v-spacer />
      <v-btn v-if="can('developer')" variant="tonal" class="text-none" prepend-icon="mdi-pencil-outline" :to="`${base}/designer`">Edit in the designer</v-btn>
    </div>
    <AxoEmptyState v-if="!doc.triggers.length" icon="mdi-lightning-bolt-outline" title="No triggers"
                   text="Runs start only by hand or from Python. Add a “Start when…” card in the designer to start them when a file arrives or an event is sent." />
    <div v-else class="axo-triggers">
      <v-card v-for="t in doc.triggers" :key="t.trigger_id" variant="outlined" class="axo-triggers__card">
        <v-card-text>
          <div class="d-flex align-center ga-3 mb-2">
            <div class="axo-triggers__disc"><v-icon :icon="t.event_type === 'file.written' ? 'mdi-file-upload-outline' : 'mdi-lightning-bolt'" color="white" /></div>
            <div class="min-w-0">
              <div class="font-weight-medium text-truncate">{{ t.event_type === 'file.written' ? `A new file in ${t.bucket}${t.prefix ? `/${t.prefix}…` : ''}` : `The event ${t.event_type}` }}</div>
              <div class="text-caption axo-muted">{{ t.condition?.rules?.length ? `when ${conditionText(t.condition)}` : 'every time' }}</div>
            </div>
          </div>
          <AxoKeyValue :columns="2" :items="[
            { label: 'Starts', value: startTargets(doc.graph, t).map(title).join(', ') || '—' },
            { label: 'Runs', value: formatNumber(stats[t.trigger_id ?? '']?.runs ?? 0) },
            { label: 'Last fired', value: stats[t.trigger_id ?? '']?.last ? timeAgo(ts(stats[t.trigger_id ?? '']!.last)) : 'never' },
            { label: 'Added', value: t.created_at ? timeAgo(ts(t.created_at)) : '—' },
          ]" />
          <div class="text-caption axo-muted mt-2">Runs act for the person who added it.</div>
        </v-card-text>
      </v-card>
    </div>
    </template>
  </div>
</template>

<script setup lang="ts">
// The active triggers (the newest version's): what starts a run, which
// steps it starts, how many runs it started and when it last fired.
import type { RunSummary } from '~/utils/choreographies'

const { id, can, api, vemUp } = useCurrentVe()
const { name, doc } = useCurrentChoreography()
const { nameOf } = useFunctions()
const base = computed(() => `/ves/${id.value}/choreographies/${encodeURIComponent(name.value)}`)
const title = (nodeId: string) => { const n = doc.value?.graph.nodes.find(x => x.node_id === nodeId); return n ? nodeTitle(n, nameOf) : nodeId }
const stats = ref<Record<string, { runs: number; last: string | null }>>({})
async function load() {
  if (!vemUp.value) return
  try {
    const runs = await api<RunSummary[]>(`/choreographies/${encodeURIComponent(name.value)}/runs`, { query: { limit: 500 } })
    const out: Record<string, { runs: number; last: string | null }> = {}
    for (const r of runs) {
      if (r.started_by_type !== 'trigger' || !r.started_by_id) continue
      const s = (out[r.started_by_id] ??= { runs: 0, last: null })
      s.runs++
      if (!s.last || (r.queued_at ?? '') > s.last) s.last = r.queued_at ?? null
    }
    stats.value = out
  } catch { stats.value = {} }
}
watch([name, vemUp], load, { immediate: true })
usePolling(load, 30_000)
</script>

<style scoped>
.axo-triggers { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.axo-triggers__disc { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; flex: 0 0 auto;
  background: radial-gradient(circle at 30% 30%, rgb(var(--v-theme-warning)), rgba(var(--v-theme-warning), 0.78)); }
</style>
