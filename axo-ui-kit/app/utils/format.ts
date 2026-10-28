// Small, plain formatters used everywhere (money, sizes, durations, times).
// Times from the Control Plane are UNIX seconds, always UTC: they are only
// *shown* in the person's time zone, date / clock / number format — their
// settings (GET /me/settings, kept by useSettings). The UI's own server
// renders pages with the same settings, so server and browser write the
// same text. Signed out: DEFAULT_SETTINGS.

export interface Settings {
  language: string
  time_zone: string
  date_format: 'MM/DD/YYYY' | 'DD/MM/YYYY' | 'YYYY-MM-DD' | 'MMM D, YYYY' | 'D MMM YYYY'
  clock: '12h' | '24h'
  number_format: '1,234.56' | '1.234,56' | '1 234,56' | '1234.56'
  first_day_of_week: 'sunday' | 'monday'
  theme: 'light' | 'dark' | 'system'
  font_size: 'small' | 'normal' | 'large'
  density: 'comfortable' | 'compact'
  chart_range: '1h' | '24h' | '7d' | '30d'
  activity_window: '1h' | '24h' | '7d' | '30d'
}

/** The Control Plane's built-in defaults (domain/settings.py). */
export const DEFAULT_SETTINGS: Settings = {
  language: 'en', time_zone: 'America/Mexico_City', date_format: 'MM/DD/YYYY', clock: '12h',
  number_format: '1,234.56', first_day_of_week: 'sunday', theme: 'light', font_size: 'normal',
  density: 'comfortable', chart_range: '24h', activity_window: '24h',
}

let trying: Settings | null = null

/** The signed-in person's settings (read in render, so a change re-renders). */
export function currentSettings(): Settings {
  if (trying) return trying
  const state = (tryUseNuxtApp() as any)?.$axoSettings
  return state?.value?.settings ?? DEFAULT_SETTINGS
}

/** What `fn` writes with some settings changed, e.g. to preview a choice:
 *  `withSettings({ clock: '24h' }, () => formatTime(now))`. */
export function withSettings<T>(changes: Partial<Settings>, fn: () => T): T {
  const before = trying
  trying = { ...currentSettings(), ...changes }
  try { return fn() } finally { trying = before }
}

// ── numbers ──────────────────────────────────────────────────────────────────
const SEPARATORS: Record<Settings['number_format'], [string, string]> = {
  '1,234.56': [',', '.'], '1.234,56': ['.', ','], '1 234,56': [' ', ','], '1234.56': ['', '.'],
}

/** en-US digits ("1,234.5") written with the setting's separators. */
function separators(text: string, s = currentSettings()): string {
  const [group, point] = SEPARATORS[s.number_format] ?? SEPARATORS['1,234.56']
  return text.replace(/[,.]/g, ch => (ch === ',' ? group : point))
}

const fixed = (n: number, digits: number) => separators(n.toFixed(digits))

export function formatNumber(n: number | null | undefined, compact = true): string {
  if (n == null || Number.isNaN(n)) return '—'
  return separators(n.toLocaleString('en-US', compact && Math.abs(n) >= 10_000
    ? { notation: 'compact', maximumFractionDigits: 1 }
    : { maximumFractionDigits: 2 }))
}

function dollars(amount: number): string {
  const text = separators(Math.abs(amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
  return `${amount < 0 ? '-' : ''}$${text}`
}

export function formatMoney(cents: number | null | undefined, opts: { free?: boolean } = {}): string {
  if (cents == null) return '—'
  if (cents === 0 && opts.free) return 'Free'
  return dollars(cents / 100)
}

/** Micro-cents (1e-6 cent): precise per-attempt costs, written in full with
 *  as many decimals as needed, up to 8 (= 1 micro-cent, so nothing is lost):
 *  $12.00, $0.0015, $0.00000015 -- never scientific notation. */
export function formatUcents(ucents: number | null | undefined): string {
  if (ucents == null) return '—'
  const amount = ucents / 1e8
  const text = separators(Math.abs(amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8 }))
  return `${amount < 0 ? '-' : ''}$${text}`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (Math.abs(v) >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${fixed(v, v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatMemoryMb(mb: number | null | undefined): string {
  return mb == null ? '—' : formatBytes(mb * 1024 * 1024)
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms < 1) return `${fixed(ms, 2)} ms`
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${fixed(ms / 1000, ms < 10_000 ? 2 : 1)} s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return m < 60 ? `${m} min ${s} s` : `${Math.floor(m / 60)} h ${m % 60} min`
}

/** A long span in words: "3 d 4 h", "2 h 5 min", "40 s". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h ${m % 60} min`
  return `${Math.floor(h / 24)} d ${h % 24} h`
}

export function formatPercent(p: number | null | undefined, digits = 0): string {
  return p == null || Number.isNaN(p) ? '—' : `${fixed(p, digits)} %`
}

// ── dates and times ──────────────────────────────────────────────────────────
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const clocks = new Map<string, Intl.DateTimeFormat>()

function clockOf(timeZone: string): Intl.DateTimeFormat {
  let f = clocks.get(timeZone)
  if (!f) {
    try {
      f = new Intl.DateTimeFormat('en-US', { timeZone, hourCycle: 'h23', year: 'numeric', month: 'numeric',
        day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' })
    } catch {
      f = clockOf('UTC')                    // a zone this browser doesn't know
    }
    clocks.set(timeZone, f)
  }
  return f
}

/** The wall clock in the setting's time zone at `ts`. */
function wall(ts: number, s: Settings) {
  const out: Record<string, number> = {}
  for (const p of clockOf(s.time_zone).formatToParts(new Date(ts * 1000))) {
    if (p.type !== 'literal') out[p.type] = Number(p.value)
  }
  return { y: out.year!, m: out.month!, d: out.day!, h: out.hour! % 24, min: out.minute!, sec: out.second! }
}

const two = (n: number) => String(n).padStart(2, '0')

function day(w: ReturnType<typeof wall>, s: Settings): string {
  switch (s.date_format) {
    case 'DD/MM/YYYY': return `${two(w.d)}/${two(w.m)}/${w.y}`
    case 'YYYY-MM-DD': return `${w.y}-${two(w.m)}-${two(w.d)}`
    case 'MMM D, YYYY': return `${MONTHS[w.m - 1]} ${w.d}, ${w.y}`
    case 'D MMM YYYY': return `${w.d} ${MONTHS[w.m - 1]} ${w.y}`
    default: return `${two(w.m)}/${two(w.d)}/${w.y}`
  }
}

/** Day and month only (chart axes): "10/19", "19/10", "Oct 19", "19 Oct". */
function shortDay(w: ReturnType<typeof wall>, s: Settings): string {
  switch (s.date_format) {
    case 'DD/MM/YYYY': return `${two(w.d)}/${two(w.m)}`
    case 'YYYY-MM-DD': return `${two(w.m)}-${two(w.d)}`
    case 'MMM D, YYYY': return `${MONTHS[w.m - 1]} ${w.d}`
    case 'D MMM YYYY': return `${w.d} ${MONTHS[w.m - 1]}`
    default: return `${two(w.m)}/${two(w.d)}`
  }
}

function clock(w: ReturnType<typeof wall>, s: Settings, seconds = false): string {
  const tail = seconds ? `:${two(w.sec)}` : ''
  if (s.clock === '24h') return `${two(w.h)}:${two(w.min)}${tail}`
  return `${w.h % 12 || 12}:${two(w.min)}${tail} ${w.h < 12 ? 'AM' : 'PM'}`
}

export function formatDate(ts: number | null | undefined): string {
  if (!ts) return '—'
  const s = currentSettings()
  return day(wall(ts, s), s)
}

export function formatTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  const s = currentSettings()
  return clock(wall(ts, s), s)
}

export function formatDateTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  const s = currentSettings()
  const w = wall(ts, s)
  return `${day(w, s)}, ${clock(w, s)}`
}

/** To the second, e.g. for a tooltip. */
export function formatExactTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  const s = currentSettings()
  const w = wall(ts, s)
  return `${day(w, s)}, ${clock(w, s, true)}`
}

/** A chart's time axis label: the time within a day or two, else the day. */
export function formatAxisTime(ts: number, spanSeconds: number): string {
  const s = currentSettings()
  const w = wall(ts, s)
  if (spanSeconds <= 2 * 86400) return w.h === 0 && w.min === 0 ? shortDay(w, s) : clock(w, s)
  return shortDay(w, s)
}

/** The short name of the setting's time zone right now, e.g. "CST", "GMT+2". */
export function timeZoneLabel(ts = Date.now() / 1000): string {
  const { time_zone } = currentSettings()
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone: time_zone, timeZoneName: 'short' })
      .formatToParts(new Date(ts * 1000)).find(p => p.type === 'timeZoneName')?.value ?? time_zone
  } catch {
    return time_zone
  }
}

/** The weekdays (0 = Monday … 6 = Sunday) in the order of the person's week. */
export function weekOrder(): number[] {
  return currentSettings().first_day_of_week === 'monday' ? [0, 1, 2, 3, 4, 5, 6] : [6, 0, 1, 2, 3, 4, 5]
}

export function timeAgo(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return '—'
  const d = Math.round(now - ts)
  const future = d < 0
  const a = Math.abs(d)
  const text = a < 45 ? 'a few seconds'
    : a < 3600 ? `${Math.round(a / 60)} min`
      : a < 86400 ? `${Math.round(a / 3600)} h`
        : a < 30 * 86400 ? `${Math.round(a / 86400)} d`
          : formatDate(ts)
  if (a >= 30 * 86400) return text
  return future ? `in ${text}` : `${text} ago`
}

/** Change between two values as a percentage (null when unknown). */
export function percentChange(now: number | null | undefined, before: number | null | undefined): number | null {
  if (now == null || before == null) return null
  if (before === 0) return now === 0 ? 0 : null
  return ((now - before) / Math.abs(before)) * 100
}

// ── notifications ────────────────────────────────────────────────────────────
export interface NoticeField { type: 'date' | 'datetime' | 'money' | string; value: any }

/** A notification's text in the current settings: its `template` with each
 *  `{name}` from `fields`, else the text the Control Plane wrote. */
export function noticeText(n: { text: string; template?: string; fields?: Record<string, NoticeField> }): string {
  if (!n.template || !n.fields) return n.text
  return n.template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const f = n.fields![name]
    if (!f) return whole
    if (f.value == null) return '?'
    if (f.type === 'date') return formatDate(f.value)
    if (f.type === 'datetime') return formatDateTime(f.value)
    if (f.type === 'money') return formatMoney(f.value)
    return String(f.value)
  })
}
