<template>
  <span v-if="state === 'complete'" class="text-body-2 axo-muted">Complete</span>
  <v-chip v-else size="x-small" variant="tonal" :color="state === 'uploading' ? 'warning' : 'error'"
          :prepend-icon="state === 'uploading' ? 'mdi-progress-upload' : 'mdi-alert-circle-outline'">
    {{ state === 'uploading' ? 'Uploading' : state === 'failed' ? 'Failed' : state }}
  </v-chip>
</template>

<script setup lang="ts">
// A file version's state: complete (quiet), still uploading, or failed.
defineProps<{ state?: string | null }>()
</script>
