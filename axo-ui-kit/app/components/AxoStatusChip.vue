<template>
  <v-chip :color="look.color" :size="size" variant="tonal" class="font-weight-medium">
    <AxoRunningRing v-if="look.ring" :size="13" :color="look.color" class="me-2" />
    <v-icon v-else start :icon="look.icon" :class="{ 'axo-pulse': look.moving }" size="16" />
    {{ label ?? look.label ?? value ?? '—' }}
    <v-tooltip v-if="reason" activator="parent">{{ reason }}</v-tooltip>
  </v-chip>
</template>

<script setup lang="ts">
import { statusLook, type StatusKind } from '../utils/status'

// A status with its color and icon (pulses while it is changing, e.g.
// Starting; Running shows the running ring). `reason`: shown on hover (why it is in that state).
const props = withDefaults(defineProps<{
  kind?: StatusKind
  value?: string | null
  label?: string
  reason?: string | null
  size?: 'x-small' | 'small' | 'default'
}>(), { kind: 'lifecycle', size: 'small' })
const look = computed(() => statusLook(props.kind, props.value))
</script>
