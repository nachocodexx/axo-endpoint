<template>
  <div>
    <div class="d-flex align-center mb-3">
      <span class="text-body-2 axo-muted">Versions saved, runs started and finished, who sees its runs.</span>
      <v-spacer />
      <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-refresh" :loading="loading" @click="feed.load">Refresh</v-btn>
    </div>
    <AxoErrorAlert v-if="error" :error="error" title="The history couldn't load" :retry="feed.load" class="mb-3" />
    <AxoSkeleton v-else-if="loading && !rows.length" :lines="6" />
    <AxoEmptyState v-else-if="!rows.length" icon="mdi-timeline-text-outline" title="Nothing yet" text="Saves and runs show here." />
    <v-timeline v-else density="compact" side="end" truncate-line="both" class="axo-activity">
      <v-timeline-item v-for="row in rows" :key="row.event_id" size="x-small" :dot-color="/Failed|failed/.test(row.event_type + JSON.stringify(row.meta?.status ?? '')) ? 'error' : ACTIVITY_AREAS[areaOf(row)]?.color">
        <div class="d-flex flex-wrap align-center ga-2">
          <span class="text-body-2">{{ describeActivity(row, () => name) }}</span>
          <NuxtLink v-if="row.meta?.run_id" :to="`/ves/${id}/choreographies/${encodeURIComponent(name)}/runs/${row.meta.run_id}`" class="axo-link text-caption axo-mono">
            {{ String(row.meta.run_id).slice(0, 12) }}</NuxtLink>
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
// What happened to this choreography, newest first, a page at a time.
const { id, vemUp } = useCurrentVe()
const { name, doc } = useCurrentChoreography()
const feed = useActivityFeed(() => (vemUp.value && doc.value ? `/choreographies/${doc.value.choreography_id}/history` : null))
const { rows, more, loading, loadingMore, error } = feed
watch(() => doc.value?.choreography_id, feed.load, { immediate: true })
watch(vemUp, (up) => { if (up) feed.load() })
usePolling(feed.refresh, 30_000)
</script>

<style scoped>
.axo-activity :deep(.v-timeline-item__body) { width: 100%; padding-bottom: 10px; }
</style>
