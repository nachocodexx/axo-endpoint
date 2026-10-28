<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-4">
      <v-chip-group v-model="areas" multiple column selected-class="text-primary">
        <v-chip v-for="(a, key) in ACTIVITY_AREAS" :key="key" :value="key" :prepend-icon="a.icon" filter variant="outlined" size="small">
          {{ a.title }}
        </v-chip>
      </v-chip-group>
      <v-spacer />
      <v-select v-model="since" :items="SINCE" density="compact" hide-details variant="outlined" style="max-width: 170px" />
      <v-btn icon="mdi-refresh" variant="text" :loading="loading" aria-label="Refresh" @click="feed.load" />
    </div>

    <AxoErrorAlert v-if="error" :error="error" title="The activity couldn't load" :retry="feed.load" class="mb-4" />

    <v-card>
      <AxoSkeleton v-if="loading && !rows.length" :lines="8" class="pa-4" />
      <AxoEmptyState v-else-if="!vemUp" icon="mdi-power-sleep" title="The VE isn't running"
                     text="Its activity shows here while it runs." />
      <AxoEmptyState v-else-if="!rows.length" icon="mdi-timeline-text-outline"
                     :title="areas.length ? 'Nothing of that kind in this period' : 'Nothing happened here yet'"
                     text="Jobs, registrations, endpoint changes and more show up here as they happen." />
      <v-timeline v-else side="end" density="compact" align="start" truncate-line="both" class="pa-4 axo-activity">
        <v-timeline-item v-for="row in rows" :key="row.event_id" size="small" :dot-color="ACTIVITY_AREAS[areaOf(row)]!.color"
                         :icon="ACTIVITY_AREAS[areaOf(row)]!.icon" fill-dot>
          <div class="d-flex flex-wrap align-baseline ga-2">
            <span class="text-body-2" :class="{ 'text-error': failed(row) }">{{ describeActivity(row, fname) }}</span>
            <v-spacer />
            <span class="text-caption axo-muted" :title="formatExactTime(Date.parse(row.created_at) / 1000)">{{ timeAgo(Date.parse(row.created_at) / 1000) }}</span>
          </div>
          <div v-if="row.meta?.job_id" class="text-caption axo-muted axo-mono">job {{ row.meta.job_id }}</div>
        </v-timeline-item>
      </v-timeline>
      <div v-if="more" class="d-flex justify-center pb-4">
        <v-btn variant="tonal" :loading="loadingMore" prepend-icon="mdi-chevron-down" @click="feed.loadMore">Load more</v-btn>
      </div>
    </v-card>
    <div v-if="keptDays" class="text-caption axo-muted mt-2">
      This VE keeps {{ keptDays }} days of history (VE settings).
    </div>
  </div>
</template>

<script setup lang="ts">
// What happened in the VE (the VEM's history, newest first): jobs,
// functions, objects, choreographies, endpoints and the VE itself, in plain
// words. The area chips filter on the VEM, a page at a time ("Load more").
const { id, api, vemUp } = useCurrentVe()

const SINCE = [
  { title: 'Last hour', value: 60 }, { title: 'Last 24 hours', value: 1440 },
  { title: 'Last 7 days', value: 10080 }, { title: 'Last 30 days', value: 43200 },
]
// Opens with the person's activity period (Settings).
const WINDOW_MINUTES: Record<string, number> = { '1h': 60, '24h': 1440, '7d': 10080, '30d': 43200 }
const since = ref(WINDOW_MINUTES[currentSettings().activity_window] ?? 1440)
const areas = ref<string[]>([])
const names = ref<Record<string, string>>({})
const keptDays = ref<number | null>(null)

const feed = useActivityFeed(() => (vemUp.value ? '/history' : null),
  () => ({ since_minutes: since.value, areas: areas.value.join(',') || undefined }))
const { rows, more, loading, loadingMore, error } = feed

async function loadNames() {
  if (!vemUp.value) return
  try {
    const [functions, view] = await Promise.all([
      api<Array<{ function_id: string; name: string }>>('/functions'),
      api<{ effective_retention_days: number | null }>('/ve'),
    ])
    names.value = Object.fromEntries(functions.map(f => [f.function_id, f.name]))
    keptDays.value = view.effective_retention_days
  } catch { /* ids instead */ }
}
const fname = (fid?: string) => (fid ? names.value[fid] ?? `${fid.slice(0, 10)}…` : 'a function')
const failed = (row: ActivityRow) => /Failed|Crashed/.test(row.event_type) || row.meta?.outcome?.startsWith?.('failed')

watch([since, areas, id], feed.load)
watch(vemUp, (up) => { if (up) { feed.load(); loadNames() } })
usePolling(feed.refresh, 30_000)
onMounted(loadNames)
</script>

<style scoped>
.axo-activity :deep(.v-timeline-item__body) { width: 100%; padding-bottom: 12px; }
</style>
