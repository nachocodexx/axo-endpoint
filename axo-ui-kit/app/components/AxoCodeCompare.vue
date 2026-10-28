<template>
  <div class="axo-compare">
    <div v-if="!unified" class="axo-compare-heads">
      <span>{{ leftTitle }}</span><span>{{ rightTitle }}</span>
    </div>
    <div v-else class="axo-compare-heads axo-compare-heads--one">
      <span><span class="text-error">−</span> {{ leftTitle }} &nbsp; <span class="text-success">+</span> {{ rightTitle }}</span>
    </div>
    <div ref="host" class="axo-compare-body" :style="{ '--axo-code-max': maxHeight }" />
    <div v-if="same" class="text-body-2 axo-muted pa-3">The two versions have the same code.</div>
  </div>
</template>

<script setup lang="ts">
import { EditorState, type Extension } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { python } from '@codemirror/lang-python'
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { MergeView, unifiedMergeView } from '@codemirror/merge'
import { oneDark } from '@codemirror/theme-one-dark'
import { useTheme } from 'vuetify'

// Two versions of some code side by side, changes marked (one column with
// − / + lines on narrow screens). Read-only.
const props = withDefaults(defineProps<{
  left: string
  right: string
  leftTitle: string
  rightTitle: string
  maxHeight?: string
}>(), { maxHeight: '640px' })

const host = shallowRef<HTMLElement | null>(null)
const theme = useTheme()
const unified = ref(false)
const same = computed(() => props.left === props.right)
let merge: MergeView | null = null
let single: EditorView | null = null

function common(): Extension[] {
  return [lineNumbers(), python(), EditorState.readOnly.of(true), EditorView.editable.of(false),
    theme.global.current.value.dark ? oneDark : syntaxHighlighting(defaultHighlightStyle),
    EditorView.theme({ '&': { backgroundColor: 'transparent' }, '.cm-gutters': { backgroundColor: 'transparent' } })]
}

function build() {
  merge?.destroy(); single?.destroy(); merge = null; single = null
  if (!host.value) return
  host.value.innerHTML = ''
  unified.value = window.innerWidth < 760
  if (unified.value) {
    single = new EditorView({
      parent: host.value,
      state: EditorState.create({ doc: props.right,
        extensions: [...common(), unifiedMergeView({ original: props.left, mergeControls: false })] }),
    })
  } else {
    merge = new MergeView({ parent: host.value, a: { doc: props.left, extensions: common() },
      b: { doc: props.right, extensions: common() }, collapseUnchanged: { margin: 3, minSize: 6 } })
  }
}

let resizeTimer: ReturnType<typeof setTimeout> | null = null
function onResize() {
  if (resizeTimer) clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => { if ((window.innerWidth < 760) !== unified.value) build() }, 200)
}
onMounted(() => { build(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { merge?.destroy(); single?.destroy(); window.removeEventListener('resize', onResize) })
watch(() => [props.left, props.right, theme.global.current.value.dark], build)
</script>

<style scoped>
.axo-compare { border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); border-radius: 8px; overflow: hidden; font-size: 13px; }
.axo-compare-heads { display: grid; grid-template-columns: 1fr 1fr; font-size: 12px; font-weight: 500;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-compare-heads > span { padding: 6px 12px; }
.axo-compare-heads--one { grid-template-columns: 1fr; }
.axo-compare-body { max-height: var(--axo-code-max); overflow: auto; }
.axo-compare-body :deep(.cm-scroller) { font-family: var(--axo-mono, ui-monospace, monospace); }
</style>
