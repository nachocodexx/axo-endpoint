<template>
  <span class="axo-copy d-inline-flex align-center ga-1">
    <span class="axo-mono axo-selectable text-truncate" :style="{ maxWidth }">{{ text }}</span>
    <v-btn icon size="x-small" variant="text" density="compact" :aria-label="`Copy ${text}`" @click.stop="copy">
      <v-icon size="14">{{ copied ? 'mdi-check' : 'mdi-content-copy' }}</v-icon>
    </v-btn>
  </span>
</template>

<script setup lang="ts">
// An id (VE, job, endpoint...) with a copy button.
const props = withDefaults(defineProps<{ text: string; maxWidth?: string }>(), { maxWidth: '220px' })
const copied = ref(false)
async function copy() {
  try {
    await navigator.clipboard.writeText(props.text)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    useNotifier().warning("Couldn't copy to the clipboard")
  }
}
</script>
