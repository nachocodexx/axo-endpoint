<template>
  <v-btn
    v-bind="$attrs"
    :class="look === 'gradient' ? 'axo-gradient' : look === 'danger' ? 'axo-gradient-danger' : undefined"
    :variant="look === 'plain' ? variant : 'flat'"
    :color="look === 'plain' ? color : undefined"
    :loading="loading"
  >
    <slot />
    <template v-if="$slots.loader" #loader><slot name="loader" /></template>
  </v-btn>
</template>

<script setup lang="ts">
// The axo button: brand gradient by default, a red gradient for
// destructive actions, or a plain Vuetify button (`look="plain"`).
defineOptions({ inheritAttrs: false })
withDefaults(defineProps<{
  look?: 'gradient' | 'danger' | 'plain'
  variant?: 'flat' | 'text' | 'outlined' | 'tonal' | 'elevated' | 'plain'
  color?: string
  loading?: boolean
}>(), { look: 'gradient', variant: 'tonal', color: 'primary', loading: false })
</script>
