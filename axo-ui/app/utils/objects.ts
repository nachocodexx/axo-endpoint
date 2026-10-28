import type { ParamSpec } from './functions'
import type { ActivityRow } from './activity'
import { ts } from './jobs'

// Active objects as the VEM serves them: classes (GET /object-classes, one
// row per version) and objects (GET /objects), an object's saved values
// (GET /objects/{name}/state) and its history rows (unified_activity).

export interface MethodInfo {
  name: string
  params: string[]
  optional?: string[]
  defaults?: Record<string, unknown>
  wants_context?: boolean
  doc?: string | null
  /** Read from the source (type hints, defaults, docstring "Args:"). */
  params_schema?: ParamSpec[]
}

export interface ClassVersion {
  class_name: string
  version: number
  code_hash: string
  methods: MethodInfo[]
  has_upgrade: boolean
  function_id: string
  runtime_spec?: Record<string, any> | null
  /** __init__'s parameters (the "New object" form); null = it takes none. */
  init?: MethodInfo | null
  description?: string | null
  created_at?: string | null
  created_by?: string | null
}

export interface ClassRow {
  class_name: string
  latest: ClassVersion
  /** Newest first. */
  versions: ClassVersion[]
}

export interface ActiveObject {
  name: string
  object_id: string
  class_name: string
  class_version: number
  /** creating (its __init__ is running) | ready */
  status: string
  created_by?: string | null
  created_at?: string | null
  cloned_from?: string | null
  revision: number
  size_bytes: number
  calls: number
  /** null = the plan's default */
  keep_revisions?: number | null
  last_call_at?: string | null
  last_outcome?: string | null
  last_method?: string | null
}

export interface StateAttribute {
  tier: 'hot' | 'blob'
  kind: 'json' | 'bytes' | 'pickle'
  size: number
  hash: string
  /** Only small JSON values (the hot tier) come in full. */
  value?: unknown
}

export interface ObjectState {
  object_id: string
  revision: number
  size_bytes: number
  updated_at?: number | string | null
  attributes: Record<string, StateAttribute>
  /** A call is running or waiting: restore waits. */
  busy: boolean
  /** The current one and the kept past ones, newest first. */
  revisions: Array<{ revision: number; updated_at?: number | string | null; size_bytes: number }>
}

export const OBJECT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/

export function objectNameProblem(name: string): string | undefined {
  if (!name) return 'A name'
  return OBJECT_NAME_RE.test(name) ? undefined : "1 to 63 letters, digits, '.', '-' or '_' (starting with a letter or digit)"
}

export function groupClasses(all: ClassVersion[]): ClassRow[] {
  const by = new Map<string, ClassVersion[]>()
  for (const v of all) (by.get(v.class_name) ?? by.set(v.class_name, []).get(v.class_name)!).push(v)
  return [...by.entries()].map(([class_name, versions]) => {
    versions.sort((a, b) => b.version - a.version)
    return { class_name, latest: versions[0]!, versions }
  }).sort((a, b) => a.class_name.localeCompare(b.class_name))
}

/** A method's fields: the source's schema, else one JSON field per parameter. */
export function methodFields(m: MethodInfo | null | undefined): ParamSpec[] {
  if (!m) return []
  if (m.params_schema?.length) return m.params_schema
  const optional = new Set(m.optional ?? [])
  return m.params.map(name => ({ name, type: 'json', required: !optional.has(name), default: m.defaults?.[name] }))
}

export const methodNames = (v: { methods: MethodInfo[] }) => v.methods.map(m => m.name).join(', ')

/** A saved time (epoch seconds, or ISO text) as epoch seconds. */
export const atSeconds = (t: number | string | null | undefined) => (typeof t === 'number' ? t : ts(t ?? null))

export const KIND_TITLES: Record<string, string> = { json: 'JSON', bytes: 'bytes', pickle: 'Python object' }

/** What kind of value: "list of 3", "text", "number", ... (JSON values), or the saved kind. */
export function valueKind(a: StateAttribute): string {
  if (!('value' in a)) return KIND_TITLES[a.kind] ?? a.kind
  const v = a.value
  if (v === null) return 'empty (None)'
  if (Array.isArray(v)) return `list of ${v.length}`
  if (typeof v === 'object') return `dict of ${Object.keys(v as object).length}`
  if (typeof v === 'string') return 'text'
  if (typeof v === 'boolean') return 'yes / no'
  return 'number'
}

/** A value in one short line (the table cell); the tree shows it all. */
export function valuePreview(v: unknown, max = 80): string {
  const text = typeof v === 'string' ? JSON.stringify(v) : JSON.stringify(v) ?? String(v)
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

/** The platform's own steps in an object's history, by method. */
export const PLATFORM_METHODS: Record<string, string> = { __create__: 'Created', __upgrade__: 'Upgraded', __restore__: 'Restored' }

/** One history row in plain words: what happened, and for a call its method. */
export function objectEventTitle(row: ActivityRow): string {
  const m = row.meta ?? {}
  switch (row.event_type) {
    case 'ActiveObjectCreated': return m.cloned_from ? `Cloned from ${m.cloned_from}` : 'Created'
    case 'ActiveObjectUpgraded': return `Upgraded v${m.from_version} → v${m.to_version}`
    case 'ActiveObjectDeleted': return 'Deleted'
    case 'ActiveObjectCalled':
      if (m.method === '__create__') return m.outcome === 'completed' ? '__init__ ran' : "__init__ failed: it wasn't created"
      if (m.method === '__restore__') return m.restored_from != null ? `Restored to revision ${m.restored_from}` : 'Restored'
      return m.method ?? 'run'
    default: return row.event_type
  }
}

export const isCall = (row: ActivityRow) => row.event_type === 'ActiveObjectCalled' && !String(row.meta?.method ?? '').startsWith('__')
export const failed = (row: ActivityRow) => row.event_type === 'ActiveObjectCalled' && row.meta?.outcome && row.meta.outcome !== 'completed'

export const CLASS_STARTER = `from axo import Axo, axo_method


class ShoppingCart(Axo):
    """A customer's cart: the items and their total."""

    def __init__(self, owner: str, currency: str = "MXN"):
        self.owner = owner
        self.currency = currency
        self.items = []
        self.total = 0.0

    @axo_method
    def add_item(self, sku: str, qty: int = 1, price: float = 10.0):
        """Adds a product.

        Args:
            sku: the product code
            qty: how many
            price: the price of one
        """
        self.items.append({"sku": sku, "qty": qty, "price": price})
        self.total = sum(i["qty"] * i["price"] for i in self.items)
        return {"items": len(self.items), "total": self.total}

    # Runs once when an object moves to this version from an older one.
    # def upgrade(self, old_version):
    #     self.coupons = []
`

