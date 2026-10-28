// Uploads from the bucket pages, in pieces: the Control Plane starts a new
// version, then the browser sends each piece (8 MB) and retries any that
// fail on its own -- a dropped connection doesn't restart the whole file.
// The queue lives as long as the tab (moving to any page keeps it going;
// a reload or closing the tab asks first); two files at a time, three
// pieces of each at once. BucketUploadPanel (bottom right) and
// BucketUploadButton (top bar) show it.

export interface Upload {
  id: number
  bucket: string                 // owner/name
  name: string                   // its path in the bucket
  size: number
  sent: number                   // bytes the server confirmed
  state: 'waiting' | 'uploading' | 'done' | 'failed' | 'cancelled'
  version?: number
  error?: unknown
}

/** The panel: open, shrunk to its header, or hidden (the top-bar button opens it again). */
export type UploadPanel = 'open' | 'minimized' | 'closed'

const FILES_AT_ONCE = 2
const PIECES_AT_ONCE = 3
const TRIES = 4
const RATE_WINDOW_MS = 10_000

const uploads = ref<Upload[]>([])
const panel = ref<UploadPanel>('closed')
const files = new Map<number, File>()            // kept until done / cancelled / cleared, so Retry works
const aborts = new Map<number, AbortController>()
const listeners = new Set<(u: Upload) => void>()
let nextId = 1

// Speed over the last few seconds, for "about N min left".
const rate = ref(0)
let sentTotal = 0
let samples: Array<[number, number]> = []
let sampler: ReturnType<typeof setInterval> | null = null

const isActive = (u: Upload) => u.state === 'waiting' || u.state === 'uploading'
const active = computed(() => uploads.value.filter(isActive))

function sample() {
  const now = Date.now()
  samples.push([now, sentTotal])
  samples = samples.filter(([t]) => now - t <= RATE_WINDOW_MS)
  const [t0, s0] = samples[0]!
  rate.value = now > t0 ? ((sentTotal - s0) * 1000) / (now - t0) : 0
  if (!active.value.length && sampler) { clearInterval(sampler); sampler = null; samples = []; rate.value = 0 }
}

let guarded = false
function guardLeaving() {
  if (guarded || typeof window === 'undefined') return
  guarded = true
  window.addEventListener('beforeunload', (ev) => {
    if (!active.value.length) return
    ev.preventDefault()
    ev.returnValue = ''            // the browser asks "Leave site?"; uploads stop if they leave
  })
}

export function useBucketUploads() {
  const { apiFetch } = useApi()

  /** Queues files (each with its path in the bucket); shows the panel again if it was closed. */
  function add(bucket: string, picked: { file: File; name: string }[], openAs: UploadPanel = 'open') {
    for (const { file, name } of picked) {
      const upload = reactive<Upload>({ id: nextId++, bucket, name, size: file.size, sent: 0, state: 'waiting' })
      uploads.value.push(upload)
      files.set(upload.id, file)
    }
    if (panel.value === 'closed') panel.value = openAs
    guardLeaving()
    pump()
  }

  function pump() {
    const running = uploads.value.filter(u => u.state === 'uploading').length
    const next = uploads.value.filter(u => u.state === 'waiting').slice(0, Math.max(0, FILES_AT_ONCE - running))
    for (const u of next) run(u)
    if (active.value.length && !sampler) { samples = [[Date.now(), sentTotal]]; sampler = setInterval(sample, 1000) }
  }

  async function run(upload: Upload) {
    const file = files.get(upload.id)!
    const abort = new AbortController()
    aborts.set(upload.id, abort)
    upload.state = 'uploading'
    try {
      const started = await apiFetch<{ version: number; piece_bytes: number; pieces: number }>(
        `/buckets/${upload.bucket}/uploads`, { method: 'POST', body: { name: upload.name, size: file.size }, signal: abort.signal })
      upload.version = started.version
      const pending = Array.from({ length: started.pieces }, (_, i) => i)
      const worker = async () => {
        while (pending.length) {
          const index = pending.shift()!
          const piece = file.slice(index * started.piece_bytes, (index + 1) * started.piece_bytes)
          await sendPiece(upload, index, piece, abort.signal)
          upload.sent += piece.size
          sentTotal += piece.size
        }
      }
      await Promise.all(Array.from({ length: Math.min(PIECES_AT_ONCE, started.pieces) }, worker))
      upload.state = 'done'
      files.delete(upload.id)
    } catch (err) {
      if (abort.signal.aborted) return
      upload.state = 'failed'
      upload.error = err
      dropVersion(upload)
    } finally {
      aborts.delete(upload.id)
      if (!abort.signal.aborted) listeners.forEach(fn => fn(upload))
      pump()
    }
  }

  async function sendPiece(upload: Upload, index: number, piece: Blob, signal: AbortSignal) {
    for (let attempt = 1; ; attempt++) {
      try {
        await apiFetch(`/buckets/${upload.bucket}/uploads/${upload.version}/pieces/${index}`, {
          method: 'PUT', body: piece, query: { file: upload.name }, signal,
          headers: { 'Content-Type': 'application/octet-stream' },
        })
        return
      } catch (err: any) {
        const status = err?.status
        // A refusal (no room, no access, bad piece) won't change by trying again.
        if (signal.aborted || attempt >= TRIES || (status > 0 && status < 500 && status !== 408 && status !== 429)) throw err
        await new Promise(r => setTimeout(r, 500 * 2 ** attempt))
      }
    }
  }

  /** The half-sent version shouldn't stay behind as "uploading". */
  function dropVersion(upload: Upload) {
    if (upload.version == null) return
    apiFetch(`/buckets/${upload.bucket}/uploads/${upload.version}`, { method: 'DELETE', query: { file: upload.name } })
      .catch(() => { /* storage marks it failed later anyway */ })
  }

  function cancel(id: number) {
    const upload = uploads.value.find(u => u.id === id)
    if (!upload || !isActive(upload)) return
    aborts.get(id)?.abort()
    upload.state = 'cancelled'
    dropVersion(upload)
    files.delete(id)
    listeners.forEach(fn => fn(upload))
    pump()
  }

  /** Sends a failed file again, from the start (a new version). */
  function retry(id: number) {
    const upload = uploads.value.find(u => u.id === id)
    if (!upload || upload.state !== 'failed' || !files.has(id)) return
    Object.assign(upload, { state: 'waiting', sent: 0, version: undefined, error: undefined })
    pump()
  }

  /** Drops finished, failed and cancelled rows from the list. */
  function clear() {
    for (const u of uploads.value) if (!isActive(u)) files.delete(u.id)
    uploads.value = uploads.value.filter(isActive)
  }

  /** Runs `fn` when an upload ends (done / failed / cancelled); call the returned function to stop. */
  function onEnd(fn: (u: Upload) => void): () => void {
    listeners.add(fn)
    return () => listeners.delete(fn)
  }

  /** What the panel's header and the top-bar button say. */
  const summary = computed(() => {
    const list = uploads.value
    const count = (s: Upload['state']) => list.filter(u => u.state === s).length
    const counted = list.filter(u => u.state !== 'cancelled' && u.state !== 'failed')
    const size = counted.reduce((s, u) => s + u.size, 0)
    const sent = counted.reduce((s, u) => s + (u.state === 'done' ? u.size : u.sent), 0)
    const left = active.value.reduce((s, u) => s + u.size - u.sent, 0)
    return {
      active: active.value.length, done: count('done'), failed: count('failed'), cancelled: count('cancelled'),
      pct: size ? (100 * sent) / size : 0,
      secondsLeft: active.value.length && rate.value > 0 ? left / rate.value : null,
    }
  })

  return { uploads, panel, summary, add, cancel, retry, clear, onEnd, canRetry: (id: number) => files.has(id) }
}

/** "about 3 min left" for the panel's header. */
export function timeLeftText(seconds: number | null): string {
  if (seconds == null) return 'Starting…'
  if (seconds < 10) return 'A few seconds left'
  if (seconds < 60) return `${Math.round(seconds / 5) * 5} s left`
  if (seconds < 3600) return `About ${Math.round(seconds / 60)} min left`
  return `About ${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min left`
}

/** "Uploading 3 files" / "5 uploads done, 1 failed". */
export function uploadTitle(s: { active: number; done: number; failed: number; cancelled: number }): string {
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
  if (s.active) return `Uploading ${plural(s.active, 'file')}`
  const parts = [s.done && `${plural(s.done, 'upload')} done`, s.failed && `${s.failed} failed`,
    s.cancelled && `${s.cancelled} cancelled`].filter(Boolean)
  return parts.join(', ') || 'Uploads'
}
