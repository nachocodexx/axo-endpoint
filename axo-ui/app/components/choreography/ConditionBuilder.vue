<template>
  <div class="axo-cond" :class="{ 'is-nested': depth > 0 }">
    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <span class="text-body-2">{{ depth ? 'A group where' : 'When' }}</span>
      <v-btn-toggle :model-value="model.match" density="compact" mandatory variant="outlined" divided color="primary"
                    @update:model-value="set(c => { c.match = $event })">
        <v-btn value="all" size="small">all</v-btn>
        <v-btn value="any" size="small">any</v-btn>
      </v-btn-toggle>
      <span class="text-body-2">of these match</span>
      <v-spacer />
      <v-btn v-if="depth" icon="mdi-close" size="x-small" variant="text" aria-label="Remove this group" @click="emit('remove')" />
    </div>

    <TransitionGroup name="axo-rule" tag="div">
      <div v-for="(r, i) in model.rules" :key="keys[i]" class="axo-cond__row">
        <ChoreographyConditionBuilder v-if="isGroup(r)" :model-value="r" :depth="depth + 1" :fields="fields" :operators="operators"
                                      @update:model-value="setRule(i, $event)" @remove="removeRule(i)" />
        <template v-else>
          <v-combobox :model-value="r.field" :items="fields" density="compact" variant="outlined" :label="fieldLabel"
                      hide-details class="axo-cond__field" placeholder="e.g. rows" @update:model-value="setRule(i, { ...r, field: String($event ?? '') })" />
          <v-select :model-value="r.op" :items="opItems" density="compact" variant="outlined" label="Is" hide-details
                    class="axo-cond__op" @update:model-value="setOp(i, r, $event)" />
          <v-text-field v-if="!NO_VALUE_OPS.includes(r.op)" :model-value="valueText(r.value)" density="compact" variant="outlined"
                        :label="listOp(r.op) ? 'Values (comma between)' : 'Value'" hide-details class="axo-cond__value"
                        @update:model-value="setRule(i, { ...r, value: parseValue($event, r.op) })" />
          <v-btn icon="mdi-close" size="small" variant="text" aria-label="Remove this rule" @click="removeRule(i)" />
        </template>
      </div>
    </TransitionGroup>
    <div class="d-flex ga-2 mt-1">
      <v-btn size="small" variant="text" prepend-icon="mdi-plus" @click="addRule">Rule</v-btn>
      <v-btn v-if="depth < 2" size="small" variant="text" prepend-icon="mdi-plus-box-multiple-outline" @click="addGroup">Group</v-btn>
    </div>
  </div>
</template>

<script setup lang="ts">
// Rules on a value (an earlier step's result, or an event's data): a field
// path ("rows", "stats.total"), a comparison and a value; joined by all
// (and) or any (or); a rule can be a group, 3 levels at most. Values are
// read as JSON when they can be (12 → a number, true, "text").
import type { Condition, ConditionOperator, ConditionRule } from '~/utils/choreographies'

const props = withDefaults(defineProps<{ modelValue: Condition; depth?: number; fields?: string[]; operators: ConditionOperator[]; fieldLabel?: string }>(),
  { depth: 0, fields: () => [], fieldLabel: 'Field' })
const emit = defineEmits<{ (e: 'update:modelValue', v: Condition): void; (e: 'remove'): void }>()
const model = computed(() => props.modelValue)

// stable keys for the animation while rules are added / removed
let next = 0
const keys = ref<number[]>(props.modelValue.rules.map(() => next++))
watch(() => props.modelValue.rules.length, (n) => {
  while (keys.value.length < n) keys.value.push(next++)
  if (keys.value.length > n) keys.value = keys.value.slice(0, n)
})

const opItems = computed(() => props.operators.map(o => ({ value: o.op, title: o.label })))
const listOp = (op: string) => op === 'in' || op === 'not_in'

function set(fn: (c: Condition) => void) {
  const c: Condition = JSON.parse(JSON.stringify(props.modelValue))
  fn(c)
  emit('update:modelValue', c)
}
const setRule = (i: number, r: ConditionRule | Condition) => set(c => { c.rules[i] = r })
function removeRule(i: number) {
  keys.value.splice(i, 1)
  set(c => { c.rules.splice(i, 1) })
}
function setOp(i: number, r: ConditionRule, op: string) {
  const rule: ConditionRule = { field: r.field, op }
  if (!NO_VALUE_OPS.includes(op)) rule.value = listOp(op) ? (Array.isArray(r.value) ? r.value : r.value === undefined ? [] : [r.value]) : (Array.isArray(r.value) ? r.value[0] : r.value)
  setRule(i, rule)
}
const addRule = () => set(c => { c.rules.push({ field: props.fields[0] ?? '', op: '==', value: '' }) })
const addGroup = () => set(c => { c.rules.push({ match: 'any', rules: [{ field: '', op: '==', value: '' }] }) })

function valueText(v: unknown): string {
  if (Array.isArray(v)) return v.map(x => (typeof x === 'string' ? x : JSON.stringify(x))).join(', ')
  if (v === undefined) return ''
  return typeof v === 'string' ? v : JSON.stringify(v)
}
function one(text: string): unknown {
  const t = text.trim()
  if (!t) return ''
  try { return JSON.parse(t) } catch { return t }
}
function parseValue(text: string, op: string): unknown {
  return listOp(op) ? text.split(',').map(x => x.trim()).filter(Boolean).map(one) : one(text)
}
</script>

<style scoped>
.axo-cond.is-nested { border-left: 3px solid rgba(var(--v-theme-primary), 0.3); padding: 8px 0 4px 12px; margin: 4px 0;
  background: rgba(var(--v-theme-primary), 0.03); border-radius: 0 8px 8px 0; flex: 1 1 100%; }
.axo-cond__row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
.axo-cond__field { flex: 2 1 150px; }
.axo-cond__op { flex: 1 1 130px; }
.axo-cond__value { flex: 2 1 130px; }
.axo-rule-enter-active, .axo-rule-leave-active { transition: all 0.2s ease; }
.axo-rule-enter-from, .axo-rule-leave-to { opacity: 0; transform: translateY(-4px); }
@media (prefers-reduced-motion: reduce) { .axo-rule-enter-active, .axo-rule-leave-active { transition: none; } }
</style>
