<template>
  <v-list density="compact" class="py-0 axo-upload-list">
    <v-list-item v-for="u in uploads" :key="u.id" :class="{ 'axo-upload--done': u.state === 'done' }"
                 @click="open(u)">
      <template #prepend>
        <v-icon :color="u.state === 'failed' ? 'error' : undefined">{{ fileIcon(u.name) }}</v-icon>
      </template>
      <template #title><div class="text-body-2 text-truncate">{{ baseName(u.name) }}</div></template>
      <template #subtitle>
        <div class="text-caption text-truncate" :class="{ 'text-error': u.state === 'failed' }">
          <template v-if="u.state === 'uploading'">{{ formatBytes(u.sent) }} of {{ formatBytes(u.size) }}</template>
          <template v-else-if="u.state === 'failed'">{{ errorText(u.error) }}</template>
          <template v-else-if="u.state === 'done'">{{ formatBytes(u.size) }} · v{{ u.version }}</template>
          <template v-else>{{ u.state === 'waiting' ? 'Waiting' : 'Cancelled' }} · {{ formatBytes(u.size) }}</template>
          <span class="axo-muted"> · {{ where(u) }}</span>
        </div>
      </template>
      <template #append>
        <div class="d-flex align-center ml-2" @click.stop>
          <template v-if="u.state === 'uploading' || u.state === 'waiting'">
            <v-progress-circular :model-value="u.size ? (100 * u.sent) / u.size : 0" :indeterminate="u.state === 'waiting'"
                                 size="22" width="3" color="primary" class="axo-upload__ring" />
            <v-btn icon="mdi-close" size="x-small" variant="text" class="axo-upload__cancel"
                   :aria-label="`Cancel ${baseName(u.name)}`" @click="uploader.cancel(u.id)" />
          </template>
          <v-icon v-else-if="u.state === 'done'" color="success">mdi-check-circle</v-icon>
          <template v-else-if="u.state === 'failed'">
            <v-btn v-if="uploader.canRetry(u.id)" size="small" variant="text" color="primary" class="text-none"
                   @click="uploader.retry(u.id)">Retry</v-btn>
            <v-icon color="error">mdi-alert-circle</v-icon>
          </template>
          <v-icon v-else class="axo-muted">mdi-cancel</v-icon>
        </div>
      </template>
    </v-list-item>
  </v-list>
</template>

<script setup lang="ts">
import type { Upload } from '../composables/useBucketUploads'

// One row per upload: file type, name, progress or how it ended, and where
// it goes (bucket › folder). Cancel while it runs, Retry when it failed;
// clicking a row opens its folder.
const emit = defineEmits<{ opened: [] }>()
const uploader = useBucketUploads()
const uploads = computed(() => [...uploader.uploads.value].reverse())

const where = (u: Upload) => (folderOf(u.name) ? `${u.bucket} › ${folderOf(u.name)}` : u.bucket)
const errorText = (err: any) => err?.message ?? 'It failed'

function open(u: Upload) {
  emit('opened')
  navigateTo(bucketLink(u.bucket, '', folderOf(u.name)))
}
</script>

<style scoped>
.axo-upload-list :deep(.v-list-item__spacer) { width: 12px !important; }
.axo-upload__cancel { display: none; }
/* While it runs the ring shows; pointing at the row turns it into Cancel. */
.v-list-item:hover .axo-upload__ring, .v-list-item:focus-within .axo-upload__ring { display: none; }
.v-list-item:hover .axo-upload__cancel, .v-list-item:focus-within .axo-upload__cancel { display: inline-flex; }
@media (hover: none) {
  .axo-upload__cancel { display: inline-flex; }
}
</style>
