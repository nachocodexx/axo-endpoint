<template>
  <AxoDataTable :headers="headers" :items="rows" item-value="run_id" :searchable="false" :loading="loading && !rows.length"
                :items-per-page="-1" :row-to="(r) => `${base}/runs/${r.run_id}`" :empty-icon="'mdi-play-circle-outline'"
                :empty-title="emptyTitle" :empty-text="emptyText">
    <template #item.status="{ item }"><AxoStatusChip kind="run" :value="item.status" /></template>
    <template #item.queued_at="{ item }">
      <span class="text-body-2 text-no-wrap" :title="formatExactTime(ts(item.queued_at))">{{ timeAgo(ts(item.started_at ?? item.queued_at)) }}</span>
    </template>
    <template #item.who="{ item }">
      <span class="text-body-2 text-no-wrap">
        <v-icon :icon="item.started_by_type === 'trigger' ? 'mdi-lightning-bolt' : item.started_by_type === 'api_key' ? 'mdi-key-outline'
                  : item.started_by_type === 'choreography' ? 'mdi-layers-triple-outline' : 'mdi-account-outline'"
                size="14" :color="item.started_by_type === 'trigger' ? 'warning' : item.started_by_type === 'choreography' ? 'success' : undefined" />
        {{ whoText(item) }}
      </span>
    </template>
    <template #item.version="{ item }"><span class="text-body-2">v{{ item.version }}</span></template>
    <template #item.steps="{ item }">
      <span class="text-body-2 text-no-wrap">{{ done(item) }} done</span>
      <span v-if="item.counts?.failed" class="text-caption text-error text-no-wrap"> · {{ item.counts.failed }} failed</span>
    </template>
    <template #item.duration_ms="{ item }"><span class="text-body-2 text-no-wrap">{{ item.duration_ms != null ? formatMs(item.duration_ms) : runActive(item.status) ? '…' : '—' }}</span></template>
  </AxoDataTable>
</template>

<script setup lang="ts">
// A list of runs (newest first): status, when, who / what started it (a
// person, a key, a trigger, or another choreography's step),
// version, steps done / failed, how long. A row opens the run.
import type { RunSummary } from '~/utils/choreographies'

withDefaults(defineProps<{ rows: RunSummary[]; base: string; loading?: boolean; emptyTitle?: string; emptyText?: string }>(),
  { emptyTitle: 'No runs yet', emptyText: 'Start one with Run….' })
const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const headers = [
  { title: 'Status', key: 'status', sortable: false },
  { title: 'Started', key: 'queued_at', sortable: false },
  { title: 'Started by', key: 'who', sortable: false, ...WIDE },
  { title: 'Version', key: 'version', sortable: false, ...WIDE },
  { title: 'Steps', key: 'steps', sortable: false },
  { title: 'Took', key: 'duration_ms', sortable: false, ...WIDE },
]
const done = (r: RunSummary) => (r.counts?.completed ?? 0) + (r.counts?.completed_with_errors ?? 0)
function whoText(r: RunSummary) {
  if (r.started_by_type === 'trigger') {
    const t = r.trigger ?? {}
    return t.kind === 'file.written' ? `New file ${t.name ?? ''}`.trim() : `Event ${t.kind ?? ''}`.trim()
  }
  if (r.started_by_type === 'choreography') return `${r.parent?.choreography_name ?? 'Another choreography'} · step ${r.parent?.step ?? '?'}`
  return r.started_by_name || (r.started_by_type === 'api_key' ? 'An API key' : 'Someone')
}
</script>
