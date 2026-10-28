// Bills as the Control Plane sends them (application/billing.py VE bills,
// application/storage_metering.py storage bills) and the Billing page's
// helpers: one list of past bills, a bill's lines for the CSV download.

export interface Payer { kind: 'user' | 'team'; id: string }

export interface BillRow {
  bill_id: string
  type: 've' | 'storage'
  title: string
  period_start: number
  period_end: number
  total_cents: number
  status: string
  late: boolean
  ve_id?: string
  ve_deleted?: boolean
}

export interface PayerView {
  payer: Payer
  ves: Array<{ ve_id: string; name: string; current: any | null }>
  ve_bills: any[]
  storage: { current: any; bills: any[] }
  open_total_cents: number
  spending?: any
}

/** VE and storage bills in one list, newest first. */
export function pastBills(view: PayerView): BillRow[] {
  const ve = view.ve_bills.map((b): BillRow => ({
    bill_id: b.bill_id, type: 've', title: b.ve_name ?? b.ve_id, period_start: b.period_start, period_end: b.period_end,
    total_cents: b.total_cents, status: b.status, late: !!(b.late_only || b.late_usage?.length), ve_id: b.ve_id,
    ve_deleted: b.ve_deleted,
  }))
  const storage = view.storage.bills.map((b): BillRow => ({
    bill_id: b.bill_id, type: 'storage', title: 'Storage', period_start: b.period_start, period_end: b.period_end,
    total_cents: b.total_cents, status: b.status, late: !!b.late?.length,
  }))
  return [...ve, ...storage].sort((a, b) => b.period_end - a.period_end || a.title.localeCompare(b.title))
}

/** The open storage bill's id (this month): the bill page shows it live. */
export const storageBillId = (payer: Payer, month: string) => `storage:${payer.kind}:${payer.id}:${month}`

export const FEE_NAMES: Record<string, string> = { plan_fee: 'Plan fee', upgrade: 'Upgrade difference', renewal: 'Renewal' }

const money = (cents: number) => (cents / 100).toFixed(2)
const fromUcents = (ucents: number) => (ucents / 1e8).toFixed(8)      // 1 cent = 1 000 000 micro-cents

/** A bill's lines as CSV rows: item, detail, quantity, unit, amount (USD). */
export function billCsv(bill: any): string {
  const rows: Array<Array<string | number>> = [['item', 'detail', 'quantity', 'unit', 'amount_usd']]
  if (bill.type === 'storage') {
    for (const b of bill.storage?.buckets ?? []) rows.push(['Bucket storage', b.name ?? b.bucket_id, b.gb_months, 'GB-months', fromUcents(b.ucents)])
    const r = bill.requests ?? {}
    rows.push(['Requests', '', r.requests ?? 0, 'requests', fromUcents(r.request_ucents ?? 0)])
    rows.push(['Transfer', 'bytes read', r.bytes_read ?? 0, 'bytes', fromUcents(r.transfer_ucents ?? 0)])
    for (const l of bill.late ?? []) rows.push(['Late usage', `from ${l.from_month}`, '', '', fromUcents(l.storage_ucents + l.requests_ucents)])
  } else {
    for (const f of bill.fees ?? []) rows.push([FEE_NAMES[f.kind] ?? f.kind, `${f.plan_id} plan`, 1, '', money(f.amount_cents)])
    const u = bill.usage ?? {}
    rows.push(['Compute', '', u.core_seconds ?? 0, 'core-seconds', fromUcents(u.compute_ucents ?? 0)])
    rows.push(['Memory', '', u.gb_seconds ?? 0, 'GB-seconds', fromUcents(u.memory_ucents ?? 0)])
    rows.push(['Requests', `${u.billable_attempts ?? 0} of ${u.attempts ?? 0} jobs billable`, u.requests ?? 0, 'requests', fromUcents(u.request_ucents ?? 0)])
    if (bill.object_state?.hours) rows.push(['Object state', '', bill.object_state.gb_months, 'GB-months', fromUcents(bill.object_state.ucents)])
    for (const l of bill.late_usage ?? []) rows.push(['Late usage', `${l.attempts} jobs`, '', '', fromUcents(l.total_ucents)])
  }
  rows.push(['Total', 'usage rounded to the cent once per bill', '', '', money(bill.total_cents)])
  const cell = (v: string | number) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v))
  return rows.map(r => r.map(cell).join(',')).join('\n') + '\n'
}
