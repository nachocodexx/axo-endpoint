<template>
  <div style="min-width: 220px">
    <v-switch :model-value="modelValue != null" :label="label" color="primary" density="compact" hide-details
              @update:model-value="(on) => emit('update:modelValue', on ? (modelValue ?? 10000) : null)" />
    <v-text-field v-if="modelValue != null" :model-value="modelValue / 100" type="number" min="0" step="1" prefix="$"
                  density="compact" hide-details="auto" style="max-width: 180px" class="mt-1"
                  :rules="[(v: any) => Number(v) >= 0 || 'Not below 0']"
                  @update:model-value="(v) => emit('update:modelValue', Math.max(0, Math.round(Number(v) * 100)))" />
    <div v-else class="text-caption axo-muted">No limit</div>
  </div>
</template>

<script setup lang="ts">
// A money limit in cents that can also be "no limit" (null), edited in dollars.
defineProps<{ modelValue: number | null; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [number | null] }>()
</script>
