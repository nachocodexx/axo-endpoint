<template>
  <div>
    <template v-if="doc">
    <v-alert v-if="!can('admin')" type="info" variant="tonal" density="compact">Costs are shown to the VE's Owner and Admins.</v-alert>
    <template v-else>
      <AxoErrorAlert v-if="error" :error="error" title="The costs couldn't load" :retry="load" class="mb-4" />
      <div class="text-body-2 axo-muted mb-3">
        What its steps' jobs cost{{ data ? ` from ${formatDate(data.start)} to ${formatDate(data.end)}` : '' }} — information only: the jobs themselves are on the VE's bill.
      </div>
      <div class="axo-ch-costs__cards mb-5">
        <AxoStatCard label="Total" :value="data ? formatUcents(data.total.cost_ucents) : '—'" icon="mdi-cash-multiple" :loading="loading" />
        <AxoStatCard label="Runs" :value="data ? formatNumber(runs.length) : '—'" icon="mdi-play-circle-outline" :loading="loading" />
        <AxoStatCard label="Per run" :value="data && runs.length ? formatUcents(Math.round(data.total.cost_ucents / runs.length)) : '—'" icon="mdi-division" :loading="loading" />
        <AxoStatCard label="Jobs run" :value="data ? formatNumber(data.total.attempts) : '—'" icon="mdi-briefcase-outline" :loading="loading" />
      </div>
      <v-row>
        <v-col cols="12" md="6">
          <AxoPanel title="By step" icon="mdi-sitemap-outline" flush>
            <v-table density="compact">
              <tbody>
                <tr v-for="s in data?.by_step ?? []" :key="s.step">
                  <td class="text-body-2">{{ title(s.step) }}</td>
                  <td style="width: 40%"><v-progress-linear :model-value="pct(s.cost_ucents)" color="primary" rounded height="6" /></td>
                  <td class="text-end text-no-wrap text-body-2">{{ formatUcents(s.cost_ucents) }} <span class="axo-muted">· {{ Math.round(pct(s.cost_ucents)) }} %</span></td>
                </tr>
                <tr v-if="!data?.by_step?.length"><td class="axo-muted text-body-2">Nothing yet</td></tr>
              </tbody>
            </v-table>
          </AxoPanel>
        </v-col>
        <v-col cols="12" md="6">
          <AxoPanel title="By version" icon="mdi-source-branch" flush>
            <v-table density="compact">
              <tbody>
                <tr v-for="v in data?.by_version ?? []" :key="v.version">
                  <td class="text-body-2">v{{ v.version }}</td>
                  <td class="text-body-2 axo-muted">{{ formatNumber(v.attempts) }} jobs</td>
                  <td class="text-end text-body-2">{{ formatUcents(v.cost_ucents) }}</td>
                </tr>
                <tr v-if="!data?.by_version?.length"><td class="axo-muted text-body-2">Nothing yet</td></tr>
              </tbody>
            </v-table>
          </AxoPanel>
        </v-col>
      </v-row>
      <AxoPanel v-if="callers.length > 1 || callers.some(c => c.called_by)" title="Who started its runs" icon="mdi-layers-triple-outline" class="mt-4" flush>
        <v-table density="compact">
          <tbody>
            <tr v-for="c in callers" :key="c.called_by ?? 'hand'">
              <td class="text-body-2">
                <template v-if="c.called_by">Called by <NuxtLink class="axo-link" :to="`/ves/${id}/choreographies/${encodeURIComponent(callerName(c.called_by))}`">{{ callerName(c.called_by) }}</NuxtLink></template>
                <template v-else>By hand or a trigger</template>
              </td>
              <td style="width: 35%"><v-progress-linear :model-value="pct(c.cost_ucents)" color="success" rounded height="6" /></td>
              <td class="text-end text-no-wrap text-body-2">{{ formatUcents(c.cost_ucents) }} <span class="axo-muted">· {{ Math.round(pct(c.cost_ucents)) }} %</span></td>
            </tr>
          </tbody>
        </v-table>
      </AxoPanel>
      <AxoPanel title="Most expensive runs" icon="mdi-trending-up" class="mt-4" flush>
        <v-table density="compact">
          <tbody>
            <tr v-for="r in priciest" :key="r.run_id">
              <td><NuxtLink :to="`/ves/${id}/choreographies/${encodeURIComponent(name)}/runs/${r.run_id}`" class="axo-link axo-mono text-body-2">{{ String(r.run_id).slice(0, 12) }}</NuxtLink></td>
              <td class="text-body-2 axo-muted">{{ r.last_finished ? timeAgo(r.last_finished) : '—' }}</td>
              <td class="text-body-2 axo-muted">{{ formatMs(r.execution_ms) }} of work</td>
              <td class="text-end text-body-2">{{ formatUcents(r.cost_ucents) }}</td>
            </tr>
            <tr v-if="!priciest.length"><td class="axo-muted text-body-2">No runs cost anything yet</td></tr>
          </tbody>
        </v-table>
      </AxoPanel>
    </template>
    </template>
  </div>
</template>

<script setup lang="ts">
// What the choreography's step jobs cost this billing period: total, runs,
// per run, by step (share; a choreography step's line includes the whole run
// it called), by version, who started its runs (by hand / called by another
// choreography), the most expensive runs.
const { id, can, cp } = useCurrentVe()
const { name, doc } = useCurrentChoreography()
const { nameOf } = useFunctions()
interface Row { attempts: number; cost_ucents: number; execution_ms: number; last_finished?: number | null; [k: string]: any }
const data = ref<{ start: number; end: number; total: Row; by_step: Row[]; by_version: Row[]; runs: Row[]; by_caller?: Row[] } | null>(null)
const { byId: chById, load: loadChoreographies } = useChoreographies()
onMounted(() => loadChoreographies())
const callers = computed(() => data.value?.by_caller ?? [])
const callerName = (cid: string) => chById(cid)?.name ?? cid
const loading = ref(false)
const error = ref<unknown>(null)
async function load() {
  if (!doc.value || !can('admin')) return
  loading.value = true
  try { data.value = await cp(`/costs/choreographies/${doc.value.choreography_id}`); error.value = null } catch (err) { error.value = err } finally { loading.value = false }
}
watch(() => doc.value?.choreography_id, load, { immediate: true })
const runs = computed(() => data.value?.runs ?? [])
const priciest = computed(() => [...runs.value].sort((a, b) => b.cost_ucents - a.cost_ucents).slice(0, 10))
const pct = (u: number) => (data.value?.total.cost_ucents ? (100 * u) / data.value.total.cost_ucents : 0)
const title = (step: string) => { const n = doc.value?.graph.nodes.find(x => x.node_id === step); return n ? nodeTitle(n, nameOf) : step }
</script>

<style scoped>
.axo-ch-costs__cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
</style>
