<template>
  <div class="axo-trigger" :class="{ 'is-selected': selected, 'is-fired': fired, 'is-problem': data.problems?.length }"
       :title="data.problems?.join('\n') || data.text">
    <div class="axo-trigger__disc">
      <v-icon :icon="data.icon" size="24" />
    </div>
    <div class="axo-trigger__label text-truncate">{{ data.title }}</div>
    <Handle type="source" :position="Position.Right" class="axo-handle" />
  </div>
</template>

<script setup lang="ts">
// "Start when…": a round card at the left, drawn connected to the steps it
// starts. Lights up when it started the run being watched.
import { Handle, Position } from '@vue-flow/core'

export interface TriggerNodeData { title: string; text: string; icon: string; fired?: boolean; problems?: string[]; index: number }
const props = defineProps<{ id: string; data: TriggerNodeData; selected?: boolean }>()
const fired = computed(() => !!props.data.fired)
</script>

<style scoped>
.axo-trigger { position: relative; width: 56px; height: 56px; }
.axo-trigger__disc {
  width: 56px; height: 56px; border-radius: 50%; display: grid; place-items: center; color: #fff;
  background: radial-gradient(circle at 30% 30%, rgb(var(--v-theme-warning)), rgba(var(--v-theme-warning), 0.78));
  box-shadow: 0 3px 12px rgba(var(--v-theme-warning), 0.35); transition: box-shadow 0.25s ease, transform 0.25s ease;
}
.axo-trigger:hover .axo-trigger__disc { transform: scale(1.05); }
.is-selected .axo-trigger__disc { box-shadow: 0 0 0 4px rgba(var(--v-theme-primary), 0.3), 0 3px 12px rgba(var(--v-theme-warning), 0.35); }
.is-problem .axo-trigger__disc { background: rgba(var(--v-border-color), 0.4); box-shadow: none; }
.is-fired .axo-trigger__disc { animation: axo-glow 1.6s ease-out 2; }
.axo-trigger__label { position: absolute; top: 60px; left: 50%; transform: translateX(-50%); width: 130px; text-align: center;
  font-size: 0.72rem; color: rgb(var(--v-theme-muted)); }
.axo-handle { width: 8px; height: 8px; border: 2px solid rgb(var(--v-theme-surface)); background: rgba(var(--v-theme-warning), 0.9); }
@keyframes axo-glow { 0% { box-shadow: 0 0 0 0 rgba(var(--v-theme-warning), 0.7); } 100% { box-shadow: 0 0 0 18px rgba(var(--v-theme-warning), 0); } }
@media (prefers-reduced-motion: reduce) { .is-fired .axo-trigger__disc { animation: none; } }
</style>
