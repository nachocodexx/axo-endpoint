<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-3">
      <v-chip-group v-model="areas" multiple selected-class="text-primary" column>
        <v-chip value="function" filter variant="outlined" size="small">Versions and settings</v-chip>
        <v-chip value="job" filter variant="outlined" size="small">Jobs</v-chip>
      </v-chip-group>
      <v-spacer />
      <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-refresh" :loading="loading" @click="feed.load">Refresh</v-btn>
    </div>
    <AxoErrorAlert v-if="error" :error="error" title="The history couldn't load" :retry="feed.load" class="mb-3" />
    <AxoSkeleton v-else-if="loading && !rows.length" :lines="6" />
    <AxoEmptyState v-else-if="!rows.length" icon="mdi-timeline-text-outline" title="Nothing yet" text="Versions, setting changes and finished jobs show here." />
    <v-timeline v-else density="compact" side="end" truncate-line="both" class="axo-activity">
      <v-timeline-item v-for="row in rows" :key="row.event_id" size="x-small" :dot-color="failed(row) ? 'error' : ACTIVITY_AREAS[areaOf(row)]?.color">
        <div class="d-flex flex-wrap align-center ga-2">
          <span class="text-body-2">{{ describeActivity(row, () => fname) }}</span>
          <NuxtLink v-if="row.meta?.job_id" :to="`/ves/${id}/jobs/${row.meta.job_id}`" class="axo-link text-caption axo-mono">
            {{ String(row.meta.job_id).slice(0, 12) }}</NuxtLink>
          <v-spacer />
          <span class="text-caption axo-muted" :title="formatExactTime(ts(row.created_at))">{{ timeAgo(ts(row.created_at)) }}</span>
        </div>
      </v-timeline-item>
    </v-timeline>
    <div v-if="more" class="text-center mt-2">
      <v-btn variant="tonal" class="text-none" :loading="loadingMore" @click="feed.loadMore">Load more</v-btn>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ActivityRow } from '~/utils/activity'

// What happened to this function: versions registered / deleted, settings
// changed, its finished jobs. Newest first, a page at a time.
const { id, vemUp } = useCurrentVe()
const { functionId, row: fn } = useCurrentFunction()
const fname = computed(() => fn.value?.name ?? 'the function')
const areas = ref<string[]>([])
const feed = useActivityFeed(() => (vemUp.value && functionId.value ? `/functions/${functionId.value}/history` : null),
  () => ({ areas: areas.value.join(',') || undefined }))
const { rows, more, loading, loadingMore, error } = feed
const failed = (r: ActivityRow) => /Failed|Crashed/.test(r.event_type)
watch([areas, functionId], feed.load)
watch(vemUp, (up) => { if (up) feed.load() })
usePolling(feed.refresh, 30_000)
</script>

<style scoped>
.axo-activity :deep(.v-timeline-item__body) { width: 100%; padding-bottom: 10px; }
</style>
