<template>
  <div v-if="password">
    <v-progress-linear :model-value="score * 25" :color="color" rounded height="6" />
    <div class="text-caption mt-1" :class="`text-${color}`">{{ label }}</div>
  </div>
</template>

<script setup lang="ts">
// A rough strength meter: length, mixed case, digits, symbols.
const props = defineProps<{ password: string }>()
const score = computed(() => {
  const p = props.password
  let s = 0
  if (p.length >= 8) s++
  if (p.length >= 12) s++
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s++
  if (/\d/.test(p) && /[^A-Za-z0-9]/.test(p)) s++
  return p.length < 8 ? Math.min(s, 1) : s
})
const color = computed(() => ['error', 'error', 'warning', 'info', 'success'][score.value]!)
// Under 8 characters the field's own rule already says so.
const label = computed(() => (props.password.length < 8 ? '' : ['Weak', 'Weak', 'Fair', 'Good', 'Strong'][score.value]))
</script>
