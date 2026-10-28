<template>
  <div class="axo-param">
    <div class="d-flex align-center ga-2 mb-1">
      <label :for="fieldId" class="text-body-2 font-weight-medium axo-mono">{{ spec.name }}</label>
      <span v-if="spec.required" class="text-error text-caption">required</span>
      <span class="text-caption axo-muted">{{ TYPE_NAMES[spec.type] ?? spec.type }}</span>
      <v-spacer />
      <v-chip v-if="!spec.required && isDefault" size="x-small" variant="tonal">default</v-chip>
      <v-btn v-else-if="!spec.required && spec.default !== undefined" size="x-small" variant="text" class="text-none"
             prepend-icon="mdi-restore" @click="emit('update:modelValue', undefined)">Use the default</v-btn>
    </div>

    <v-text-field v-if="spec.type === 'number'" :id="fieldId" :model-value="modelValue ?? ''" type="number" density="compact"
                  variant="outlined" :placeholder="placeholder" persistent-placeholder :hint="spec.description ?? undefined"
                  persistent-hint :error-messages="error" @update:model-value="onNumber" />
    <v-text-field v-else-if="spec.type === 'string'" :id="fieldId" :model-value="modelValue ?? ''" density="compact" variant="outlined"
                  :placeholder="placeholder" persistent-placeholder :hint="spec.description ?? undefined" persistent-hint
                  :error-messages="error" @update:model-value="v => emit('update:modelValue', v === '' && !spec.required ? undefined : v)" />
    <div v-else-if="spec.type === 'boolean'">
      <v-switch :id="fieldId" :model-value="current" color="primary" inset density="compact" hide-details
                :label="current ? 'Yes' : 'No'" @update:model-value="v => emit('update:modelValue', !!v)" />
      <div v-if="spec.description" class="text-caption axo-muted">{{ spec.description }}</div>
    </div>
    <div v-else-if="spec.type === 'data_ref'">
      <BucketFilePicker :model-value="refValue" :label="spec.name" @update:model-value="onRef" />
      <div v-if="spec.description" class="text-caption axo-muted mt-1">{{ spec.description }}</div>
      <div v-if="error" class="text-caption text-error mt-1">{{ error }}</div>
    </div>
    <div v-else>
      <AxoCode :model-value="jsonText" language="json" min-height="44px" max-height="220px" :label="spec.name"
               :problems="jsonProblem ? [{ line: 1, message: jsonProblem }] : []" @update:model-value="onJson" @run="emit('run')" />
      <div class="text-caption mt-1" :class="jsonProblem || error ? 'text-error' : 'axo-muted'">
        {{ jsonProblem || error || spec.description || 'Any JSON value: a number, "text", a [list] or an {object}.' }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ParamSpec } from '~/utils/functions'
import type { DataRefValue } from './BucketFilePicker.vue'

// One parameter of the run form, by its type hint: number, text, yes / no,
// a bucket file (DataRef) or any JSON. `undefined` = not set (the code's
// default applies). Errors come from the form's check or the server.
const props = defineProps<{ spec: ParamSpec; modelValue: unknown; error?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: unknown): void; (e: 'run'): void; (e: 'invalid', v: string | null): void }>()

const TYPE_NAMES: Record<string, string> = { number: 'number', string: 'text', boolean: 'yes / no', json: 'JSON', data_ref: 'bucket file' }
const fieldId = computed(() => `param-${props.spec.name}`)
const isDefault = computed(() => props.modelValue === undefined)
const current = computed(() => (props.modelValue === undefined ? props.spec.default : props.modelValue))
const placeholder = computed(() => (props.spec.default != null ? `${props.spec.default} (default)` : ''))

function onNumber(v: string | number) {
  if (v === '' || v === null) { emit('update:modelValue', undefined); return }
  const n = Number(v)
  emit('update:modelValue', Number.isFinite(n) ? n : v)
}

// JSON: the text is kept while it doesn't parse (the field shows why).
const jsonText = ref(props.modelValue === undefined ? '' : JSON.stringify(props.modelValue))
const jsonProblem = ref<string | null>(null)
watch(() => props.modelValue, (v) => {
  try { if (v !== undefined && JSON.stringify(JSON.parse(jsonText.value || 'null')) === JSON.stringify(v)) return } catch { /* typing */ }
  if (!jsonProblem.value) jsonText.value = v === undefined ? '' : JSON.stringify(v)
})
function onJson(text: string) {
  jsonText.value = text
  if (!text.trim()) { jsonProblem.value = null; emit('invalid', null); emit('update:modelValue', undefined); return }
  try {
    const value = JSON.parse(text)
    jsonProblem.value = null
    emit('invalid', null)
    emit('update:modelValue', value)
  } catch (err) {
    jsonProblem.value = `Not valid JSON: ${(err as Error).message}`
    emit('invalid', jsonProblem.value)
  }
}

const refValue = computed<DataRefValue | null>(() => {
  const v = props.modelValue as any
  return v && typeof v === 'object' && v.__axo_data__ ? v.__axo_data__ : null
})
const onRef = (v: DataRefValue | null) => emit('update:modelValue', v ? { __axo_data__: { format: 'raw', ...v } } : undefined)
</script>

<style scoped>
.axo-param + .axo-param { margin-top: 16px; }
</style>
