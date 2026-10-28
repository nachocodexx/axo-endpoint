import type { ChGraph, ChSettings } from '../utils/choreographies'

export interface DraftContent { graph: ChGraph; name: string; description: string; settings: ChSettings }
interface Stored extends DraftContent { base: number; savedAt: number }

const LIMIT = 60

/** The designer's unsaved changes: kept in this browser (localStorage, per
 *  VE + choreography) until saved or discarded, with undo / redo. `base` =
 *  the version the changes started from (a newer saved version = someone
 *  saved meanwhile: the conflict choice). Storage may be missing or full: the
 *  draft then lives only while the page is open. */
export function useChoreographyDraft(key: MaybeRefOrGetter<string>) {
  const content = ref<DraftContent | null>(null)
  const base = ref(0)
  const savedAt = ref<number | null>(null)
  const undoStack = ref<string[]>([])
  const redoStack = ref<string[]>([])
  let clean = ''                       // what the draft started from (no changes = not dirty)

  const storageKey = computed(() => `axo-ch-draft:${toValue(key)}`)
  const snapshot = () => JSON.stringify(content.value)
  const dirty = computed(() => content.value !== null && snapshot() !== clean)

  function read(): Stored | null {
    try {
      const raw = localStorage.getItem(storageKey.value)
      return raw ? JSON.parse(raw) as Stored : null
    } catch { return null }
  }
  function write() {
    if (!content.value) return
    try {
      if (!dirty.value) localStorage.removeItem(storageKey.value)
      else localStorage.setItem(storageKey.value, JSON.stringify({ ...content.value, base: base.value, savedAt: Date.now() }))
      savedAt.value = dirty.value ? Date.now() : null
    } catch { /* no storage: the draft lives while the page is open */ }
  }

  /** Starts from `from` (the saved version), or from the browser's draft of
   *  it when there is one. Returns true when a kept draft was restored. */
  function start(from: DraftContent, version: number): boolean {
    clean = JSON.stringify(from)
    const kept = import.meta.client ? read() : null
    undoStack.value = []
    redoStack.value = []
    if (kept) {
      const { base: b, savedAt: at, ...rest } = kept
      content.value = rest
      base.value = b
      savedAt.value = at
      return true
    }
    content.value = JSON.parse(clean)
    base.value = version
    savedAt.value = null
    return false
  }

  /** One change (undoable): `fn` edits a copy of the draft. */
  function change(fn: (d: DraftContent) => void) {
    if (!content.value) return
    const before = snapshot()
    const next = JSON.parse(before) as DraftContent
    fn(next)
    if (JSON.stringify(next) === before) return
    undoStack.value = [...undoStack.value.slice(-LIMIT + 1), before]
    redoStack.value = []
    content.value = next
    write()
  }
  function undo() {
    const prev = undoStack.value.at(-1)
    if (prev === undefined) return
    redoStack.value = [...redoStack.value, snapshot()]
    undoStack.value = undoStack.value.slice(0, -1)
    content.value = JSON.parse(prev)
    write()
  }
  function redo() {
    const next = redoStack.value.at(-1)
    if (next === undefined) return
    undoStack.value = [...undoStack.value, snapshot()]
    redoStack.value = redoStack.value.slice(0, -1)
    content.value = JSON.parse(next)
    write()
  }
  /** Saved (as `version`) or thrown away: the draft is the saved one again. */
  function reset(to: DraftContent, version: number) {
    try { localStorage.removeItem(storageKey.value) } catch { /* no storage */ }
    clean = JSON.stringify(to)
    content.value = JSON.parse(clean)
    base.value = version
    savedAt.value = null
    undoStack.value = []
    redoStack.value = []
  }

  return {
    content, base, savedAt, dirty, start, change, undo, redo, reset,
    canUndo: computed(() => undoStack.value.length > 0), canRedo: computed(() => redoStack.value.length > 0),
  }
}
