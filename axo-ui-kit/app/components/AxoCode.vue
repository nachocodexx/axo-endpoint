<template>
  <div ref="host" class="axo-code" :class="{ 'axo-code--readonly': readonly }"
       :style="{ '--axo-code-min': minHeight, '--axo-code-max': maxHeight }">
    <pre v-if="!ready" class="axo-code-fallback">{{ modelValue }}</pre>
  </div>
</template>

<script setup lang="ts">
import { Compartment, EditorState, type Extension } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { basicSetup } from 'codemirror'
import { python } from '@codemirror/lang-python'
import { json } from '@codemirror/lang-json'
import { setDiagnostics, type Diagnostic } from '@codemirror/lint'
import { oneDark } from '@codemirror/theme-one-dark'
import { useTheme } from 'vuetify'

export interface CodeProblem { line?: number | null; column?: number | null; message: string; severity?: 'error' | 'warning' }

// Code (Python or JSON) in CodeMirror 6: read-only views (the Code tab, a
// job's result) and editors (New function, a run's JSON). Follows the
// light / dark theme; `problems` marks lines (the live check); Ctrl/Cmd +
// Enter emits `run`. Built in the browser only: the server renders the
// plain text, so nothing jumps.
const props = withDefaults(defineProps<{
  modelValue: string
  language?: 'python' | 'json' | 'text'
  readonly?: boolean
  problems?: CodeProblem[]
  minHeight?: string
  maxHeight?: string
  label?: string
}>(), { language: 'python', readonly: false, problems: () => [], minHeight: '120px', maxHeight: '640px' })
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void; (e: 'run'): void }>()

const host = shallowRef<HTMLElement | null>(null)
const ready = ref(false)
const theme = useTheme()
const dark = computed(() => theme.global.current.value.dark)
let view: EditorView | null = null
const themeSlot = new Compartment()
const editSlot = new Compartment()
const langSlot = new Compartment()

const lightTheme = EditorView.theme({
  '&': { backgroundColor: 'transparent' },
  '.cm-gutters': { backgroundColor: 'transparent', borderRight: '1px solid rgba(0,0,0,.08)' },
})
const darkTheme: Extension = [oneDark, EditorView.theme({
  '&': { backgroundColor: 'transparent' }, '.cm-gutters': { backgroundColor: 'transparent' },
}, { dark: true })]

const languageOf = (l: string): Extension => (l === 'python' ? python() : l === 'json' ? json() : [])
const editable = (ro: boolean): Extension => [EditorState.readOnly.of(ro), EditorView.editable.of(!ro)]

function diagnostics(): Diagnostic[] {
  if (!view) return []
  const doc = view.state.doc
  return props.problems.filter(p => p.line).map((p) => {
    const line = doc.line(Math.min(Math.max(1, p.line!), doc.lines))
    const from = Math.min(line.from + Math.max(0, (p.column ?? 1) - 1), line.to)
    return { from, to: Math.max(from, line.to), severity: p.severity ?? 'error', message: p.message }
  })
}

onMounted(() => {
  view = new EditorView({
    parent: host.value!,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        basicSetup,
        keymap.of([{ key: 'Mod-Enter', run: () => { emit('run'); return true } }]),
        langSlot.of(languageOf(props.language)),
        editSlot.of(editable(props.readonly)),
        themeSlot.of(dark.value ? darkTheme : lightTheme),
        EditorView.contentAttributes.of(props.label ? { 'aria-label': props.label } : {}),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
        }),
      ],
    }),
  })
  ready.value = true
  view.dispatch(setDiagnostics(view.state, diagnostics()))
})
onBeforeUnmount(() => { view?.destroy(); view = null })

watch(() => props.modelValue, (v) => {
  if (view && v !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: v } })
  }
})
watch(dark, d => view?.dispatch({ effects: themeSlot.reconfigure(d ? darkTheme : lightTheme) }))
watch(() => props.readonly, ro => view?.dispatch({ effects: editSlot.reconfigure(editable(ro)) }))
watch(() => props.language, l => view?.dispatch({ effects: langSlot.reconfigure(languageOf(l)) }))
watch(() => props.problems, () => { if (view) view.dispatch(setDiagnostics(view.state, diagnostics())) }, { deep: true })

/** Moves the cursor to a line (a problem clicked in the list). */
function goTo(line: number) {
  if (!view) return
  const l = view.state.doc.line(Math.min(Math.max(1, line), view.state.doc.lines))
  view.dispatch({ selection: { anchor: l.from }, scrollIntoView: true })
  view.focus()
}
defineExpose({ goTo })
</script>

<style scoped>
.axo-code {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  overflow: hidden;
  font-size: 13px;
}
.axo-code :deep(.cm-editor) { min-height: var(--axo-code-min); max-height: var(--axo-code-max); }
.axo-code :deep(.cm-scroller) { font-family: var(--axo-mono, ui-monospace, SFMono-Regular, Menlo, monospace); }
.axo-code :deep(.cm-editor.cm-focused) { outline: none; }
.axo-code:focus-within { border-color: rgb(var(--v-theme-primary)); }
.axo-code--readonly:focus-within { border-color: rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-code-fallback {
  margin: 0; padding: 4px 8px 4px 40px; min-height: var(--axo-code-min); max-height: var(--axo-code-max);
  overflow: auto; font-family: var(--axo-mono, ui-monospace, monospace); white-space: pre;
}
</style>
