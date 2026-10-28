<template>
  <v-menu v-if="uploader.uploads.value.length" v-model="menu" location="bottom end" :close-on-content-click="false">
    <template #activator="{ props: activator }">
      <v-btn v-bind="activator" icon variant="text" color="white" :aria-label="title">
        <v-progress-circular v-if="s.active" :model-value="s.pct" size="34" width="2" color="primary" bg-color="rgba(255,255,255,0.2)">
          <v-icon size="18">mdi-upload</v-icon>
        </v-progress-circular>
        <v-badge v-else :color="s.failed ? 'error' : 'success'" dot>
          <v-icon>mdi-upload</v-icon>
        </v-badge>
      </v-btn>
    </template>
    <v-card rounded="lg" width="380" max-width="calc(100vw - 32px)">
      <div class="d-flex align-center px-4 pt-3 pb-2">
        <div class="flex-grow-1" style="min-width: 0">
          <div class="text-body-2 font-weight-bold text-truncate">{{ title }}</div>
          <div v-if="s.active" class="text-caption axo-muted">{{ timeLeftText(s.secondsLeft) }}</div>
        </div>
      </div>
      <v-progress-linear v-if="s.active" :model-value="s.pct" height="3" color="primary" />
      <v-divider v-else />
      <div style="max-height: 360px; overflow-y: auto"><BucketUploadList @opened="menu = false" /></div>
      <v-divider />
      <div class="d-flex align-center pa-2">
        <v-btn v-if="s.done || s.failed || s.cancelled" size="small" variant="text" class="text-none" @click="uploader.clear()">
          Clear finished
        </v-btn>
        <v-spacer />
        <v-btn size="small" variant="text" color="primary" class="text-none" prepend-icon="mdi-dock-bottom" @click="show">
          Show panel
        </v-btn>
      </div>
    </v-card>
  </v-menu>
</template>

<script setup lang="ts">
import { timeLeftText, uploadTitle } from '../composables/useBucketUploads'

// The uploads in the top bar, while this tab has any: a ring with the
// progress of what's still going; the menu lists them like the panel and
// brings the panel back. Gone once the list is cleared.
const uploader = useBucketUploads()
const menu = ref(false)
const s = computed(() => uploader.summary.value)
const title = computed(() => uploadTitle(s.value))

function show() {
  uploader.panel.value = 'open'
  menu.value = false
}
</script>
