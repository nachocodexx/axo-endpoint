<template>
  <div class="axo-snacks" aria-live="polite">
    <TransitionGroup name="axo-snack">
      <div v-for="s in visible" :key="s.id" :class="['axo-snack', `axo-snack--${s.kind}`, { 'axo-snack--link': s.to }]"
           role="status" @mouseenter="pause(s.id)" @mouseleave="resume(s.id)" @click="open(s)">
        <v-icon :color="s.kind" class="mr-3 mt-1" size="22">{{ ICONS[s.kind] }}</v-icon>
        <div class="flex-grow-1" style="min-width: 0">
          <div v-if="s.title" class="text-body-2 font-weight-bold">{{ s.title }}</div>
          <div class="text-body-2 axo-snack__text">{{ s.text }}</div>
          <div v-if="s.errorName" class="d-flex align-center flex-wrap ga-2 mt-2">
            <AxoErrorName :name="s.errorName" :code="s.errorCode" />
            <NuxtLink :to="`/errors#${s.errorName}`" class="text-caption text-primary" @click.stop="dismiss(s.id)">
              What's this?
            </NuxtLink>
          </div>
        </div>
        <v-btn v-if="s.action" size="small" variant="text" :color="s.kind" class="text-none ml-1 align-self-center"
               @click.stop="dismiss(s.id); s.action.run()">{{ s.action.text }}</v-btn>
        <v-btn icon size="x-small" variant="text" class="ml-1" aria-label="Close" @click.stop="dismiss(s.id)">
          <v-icon size="16">mdi-close</v-icon>
        </v-btn>
        <div class="axo-snack__bar" :style="{ width: `${progress(s)}%`, background: `rgb(var(--v-theme-${s.kind}))` }" />
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { MAX_VISIBLE_SNACKS, type Snack } from '../composables/useNotifier'

// Timed, stacked snackbars: at most 3 at once (the next waits), a bar shows
// the time left, hovering pauses it, clicking goes to the subject, errors
// show their name + number linked to the error catalog; an optional action
// button (Undo).
const ICONS = { success: 'mdi-check-circle', info: 'mdi-information', warning: 'mdi-alert', error: 'mdi-alert-octagon' }
const { snacks, dismiss } = useNotifier()
const visible = computed(() => snacks.value.slice(0, MAX_VISIBLE_SNACKS))

// Time left per snack, counted only while it's on screen and not hovered.
const left = reactive<Record<number, number>>({})
const paused = reactive<Record<number, boolean>>({})
let timer: ReturnType<typeof setInterval> | null = null
const TICK = 100

onMounted(() => {
  timer = setInterval(() => {
    for (const s of visible.value) {
      const remaining = (left[s.id] ?? s.timeout) - (paused[s.id] ? 0 : TICK)
      left[s.id] = remaining
      if (remaining <= 0) { dismiss(s.id); delete left[s.id]; delete paused[s.id] }
    }
  }, TICK)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

const progress = (s: Snack) => Math.max(0, Math.min(100, ((left[s.id] ?? s.timeout) / s.timeout) * 100))
const pause = (id: number) => { paused[id] = true }
const resume = (id: number) => { paused[id] = false }

function open(s: Snack) {
  if (!s.to) return
  dismiss(s.id)
  navigateTo(s.to)
}
</script>

<style scoped>
.axo-snacks {
  /* --axo-snack-offset: room taken at the bottom right by an app's own panel (axo-ui's uploads). */
  position: fixed; right: 20px; bottom: calc(20px + var(--axo-snack-offset, 0px)); z-index: 3000;
  transition: bottom 0.2s ease;
  display: flex; flex-direction: column-reverse; gap: 10px; width: min(420px, calc(100vw - 32px));
}
.axo-snack {
  position: relative; overflow: hidden; display: flex; align-items: flex-start;
  padding: 14px 10px 16px 16px; border-radius: 12px;
  background: rgb(var(--v-theme-surface)); color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
}
.axo-snack--success { border-left: 4px solid rgb(var(--v-theme-success)); }
.axo-snack--info { border-left: 4px solid rgb(var(--v-theme-info)); }
.axo-snack--warning { border-left: 4px solid rgb(var(--v-theme-warning)); }
.axo-snack--error { border-left: 4px solid rgb(var(--v-theme-error)); }
.axo-snack--link { cursor: pointer; }
.axo-snack--link:hover { box-shadow: 0 12px 34px rgba(0, 134, 255, 0.22); }
.axo-snack__text { word-break: break-word; }
.axo-snack__bar { position: absolute; left: 0; bottom: 0; height: 3px; opacity: 0.7; transition: width 0.1s linear; }
.axo-snack-enter-active, .axo-snack-leave-active { transition: all 0.25s ease; }
.axo-snack-enter-from { opacity: 0; transform: translateX(40px); }
.axo-snack-leave-to { opacity: 0; transform: translateX(40px) scale(0.96); }
</style>
