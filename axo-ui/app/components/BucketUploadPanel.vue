<template>
  <div v-if="uploader.panel.value !== 'closed' && uploader.uploads.value.length" ref="root"
       class="axo-upload-panel" role="region" aria-label="Uploads">
    <div class="axo-upload-panel__head" @click="toggle">
      <div class="flex-grow-1" style="min-width: 0">
        <div class="text-body-2 font-weight-bold text-truncate">{{ title }}</div>
        <div v-if="s.active" class="text-caption axo-upload-panel__sub">{{ timeLeftText(s.secondsLeft) }}</div>
      </div>
      <v-btn v-if="!s.active && uploader.panel.value === 'open'" size="small" variant="text" color="white" class="text-none"
             @click.stop="uploader.clear()">Clear</v-btn>
      <v-btn :icon="uploader.panel.value === 'open' ? 'mdi-chevron-down' : 'mdi-chevron-up'" size="small" variant="text" color="white"
             :aria-label="uploader.panel.value === 'open' ? 'Minimize' : 'Expand'" @click.stop="toggle" />
      <v-btn icon="mdi-close" size="small" variant="text" color="white" aria-label="Close (uploads keep going)"
             @click.stop="uploader.panel.value = 'closed'" />
    </div>
    <v-progress-linear v-if="s.active" :model-value="s.pct" height="3" color="primary" bg-color="white" />
    <div v-show="uploader.panel.value === 'open'" class="axo-upload-panel__body">
      <BucketUploadList />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useDisplay } from 'vuetify'
import { timeLeftText, uploadTitle } from '../composables/useBucketUploads'

// The uploads panel, bottom right on every page (Google Drive style):
// minimize shrinks it to its header, close hides it -- uploads keep going
// either way, and the top-bar button (BucketUploadButton) opens it again.
// A new upload opens it by itself. Snackbars stack above it while it shows.
// A failure while it's hidden or shrunk gets one snackbar that opens it.
const uploader = useBucketUploads()
const notify = useNotifier()
const { smAndDown } = useDisplay()
const s = computed(() => uploader.summary.value)
const title = computed(() => uploadTitle(s.value))

const toggle = () => { uploader.panel.value = uploader.panel.value === 'open' ? 'minimized' : 'open' }

// On a phone it starts shrunk (it would cover the page).
watch(() => uploader.panel.value, (p, before) => {
  if (before === 'closed' && p === 'open' && smAndDown.value) uploader.panel.value = 'minimized'
})

// Snackbars sit above the panel: the kit reads --axo-snack-offset.
const root = ref<HTMLElement>()
let observer: ResizeObserver | null = null
const setOffset = (px: number) => document.documentElement.style.setProperty('--axo-snack-offset', `${px}px`)
watch(root, (el) => {
  observer?.disconnect()
  if (!el) { setOffset(0); return }
  observer = new ResizeObserver(() => setOffset(el.offsetHeight + 12))
  observer.observe(el)
})

let failed = 0
let failTimer: ReturnType<typeof setTimeout> | undefined
let stop: (() => void) | undefined
onMounted(() => {
  stop = uploader.onEnd((u) => {
    if (u.state !== 'failed' || uploader.panel.value === 'open') return
    failed++
    clearTimeout(failTimer)
    failTimer = setTimeout(() => {
      const n = failed
      failed = 0
      notify.push({
        kind: 'error', text: n === 1 ? `${baseName(u.name)} couldn't be uploaded` : `${n} uploads failed`,
        action: { text: 'Show', run: () => { uploader.panel.value = 'open' } },
      })
    }, 500)
  })
})
onBeforeUnmount(() => { stop?.(); observer?.disconnect(); setOffset(0) })
</script>

<style scoped>
.axo-upload-panel {
  position: fixed; right: 20px; bottom: 20px; z-index: 2900; width: min(380px, calc(100vw - 32px));
  border-radius: 12px; overflow: hidden;
  background: rgb(var(--v-theme-surface)); color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.22);
}
.axo-upload-panel__head {
  display: flex; align-items: center; gap: 2px; padding: 8px 6px 8px 16px; cursor: pointer;
  background: #000; color: #fff;
}
.axo-upload-panel__sub { opacity: 0.75; }
.axo-upload-panel__body { max-height: 320px; overflow-y: auto; }
@media (max-width: 599px) {
  .axo-upload-panel { right: 0; bottom: 0; width: 100vw; border-radius: 12px 12px 0 0; }
}
</style>
