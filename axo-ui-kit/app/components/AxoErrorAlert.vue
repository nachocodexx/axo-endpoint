<template>
  <v-alert type="error" variant="tonal" border="start" class="my-2">
    <div class="d-flex align-center flex-wrap ga-2">
      <strong>{{ title }}</strong>
      <AxoErrorName v-if="e" :name="e.errorName" :code="e.errorCode" />
    </div>
    <div v-if="e" class="text-body-2 mt-1">{{ e.message }}</div>
    <div v-if="hint" class="text-body-2 axo-muted mt-1">{{ hint }}</div>
    <template v-if="retry" #append>
      <v-btn variant="text" size="small" prepend-icon="mdi-refresh" @click="retry">Try again</v-btn>
    </template>
  </v-alert>
</template>

<script setup lang="ts">
import { AxoApiError } from '../utils/apiError'

// A failure shown in place (a page or panel couldn't load): what failed,
// the error's name + number (linked), its catalog hint, and "Try again".
const props = withDefaults(defineProps<{ error: unknown; title?: string; retry?: () => unknown }>(),
  { title: "This couldn't be loaded" })
const e = computed(() => (props.error ? AxoApiError.from(props.error) : null))
const { lookup, load } = useErrorCatalog()
onMounted(load)
const hint = computed(() => lookup(e.value?.errorName)?.hint)
</script>
