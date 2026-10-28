import { formatMs } from 'axo-ui-kit/app/utils/format'

// One line of a VE's activity feed (the VEM's unified_activity rows) in
// plain words. `fname` turns a function id into its name.

export interface ActivityRow {
  event_id: string
  event_type: string
  /** job | function | object | choreography | endpoint | ve (the VEM's areas). */
  area?: string
  created_at: string
  endpoint_id?: string | null
  user_id?: string | null
  meta: Record<string, any>
}

export const ACTIVITY_AREAS: Record<string, { title: string; icon: string; color: string }> = {
  job: { title: 'Jobs', icon: 'mdi-play-circle-outline', color: 'primary' },
  function: { title: 'Functions', icon: 'mdi-function-variant', color: 'brand-3' },
  object: { title: 'Objects', icon: 'mdi-shape-outline', color: 'info' },
  choreography: { title: 'Choreographies', icon: 'mdi-sitemap-outline', color: 'success' },
  endpoint: { title: 'Endpoints', icon: 'mdi-server-outline', color: 'warning' },
  ve: { title: 'VE', icon: 'mdi-cube-outline', color: 'secondary' },
}

/** The row's area: the VEM's when it sent one, else worked out from the type. */
export function areaOf(row: ActivityRow): string {
  return row.area && row.area in ACTIVITY_AREAS ? row.area : activityArea(row.event_type)
}

export function activityArea(type: string): string {
  if (type.startsWith('Job')) return 'job'
  if (type.startsWith('Function')) return 'function'
  if (type.startsWith('ActiveObject')) return 'object'
  if (type.startsWith('Choreography')) return 'choreography'
  if (type.startsWith('Endpoint') || type.startsWith('Leader') || type.startsWith('Consensus') || type.startsWith('Cluster')) return 'endpoint'
  return 've'
}

const words = (type: string) => type.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase()
const ms = (v: unknown) => (typeof v === 'number' ? ` in ${formatMs(v)}` : '')

export function describeActivity(row: ActivityRow, fname: (id?: string) => string): string {
  const m = row.meta || {}
  const fn = () => (m.name ?? fname(m.function_id))
  const v = m.version != null ? ` v${m.version}` : ''
  switch (row.event_type) {
    case 'JobQueued': return `Job for ${fn()} queued`
    case 'JobStarted': return `Job for ${fn()} started`
    case 'JobCompleted': return `Job for ${fn()} completed${ms(m.duration_ms)}`
    case 'JobFailed': return `Job for ${fn()} failed${m.error ? `: ${String(m.error).slice(0, 160)}` : ''}`
    case 'FunctionRegistered': return `${fn()}${v} registered`
    case 'FunctionRegisterFailed': return `${fn()} couldn't be registered${m.error ? `: ${m.error}` : ''}`
    case 'FunctionDeployed': return `${fn()}${v} ready on ${row.endpoint_id ?? 'an endpoint'}`
    case 'FunctionActivated': return `${fn()}${v} runner started`
    case 'FunctionDeactivated': return `${fn()}${v} runner stopped (idle)`
    case 'FunctionStopped': return `${fn()}${v} stopped`
    case 'FunctionCrashed': return `${fn()}${v} runner crashed`
    case 'FunctionUpdated': return `${fn()}${v} settings changed`
    case 'FunctionDeleted': return `${fn()}${v} deleted`
    case 'FunctionDefaultVersionChanged': return `${fn()}${v} made the default${m.previous_version != null ? ` (was v${m.previous_version})` : ''}${m.changed_by_name ? ` by ${m.changed_by_name}` : ''}`
    case 'ActiveObjectClassRegistered': return `Class ${m.class_name ?? ''} v${m.class_version ?? m.version ?? '?'} registered`
    case 'ActiveObjectCreated': return `Object ${m.name ?? m.object_id} created`
    case 'ActiveObjectCalled': {
      const what = m.method === '__create__' ? 'created' : m.method === '__upgrade__' ? 'upgraded' : `.${m.method}()`
      return `Object ${m.name ?? m.object_id} ${what} — ${m.outcome ?? ''}${ms(m.duration_ms)}`
    }
    case 'ActiveObjectDeleted': return `Object ${m.name ?? m.object_id} deleted`
    case 'ChoreographyCreated': return `Choreography ${m.name ?? m.choreography_name ?? ''} created`
    case 'ChoreographyUpdated': return `Choreography ${m.name ?? m.choreography_name ?? ''} changed (v${m.version ?? '?'})`
    case 'ChoreographyRunStarted': return `${m.choreography_name ?? 'Choreography'} run started${m.started_by_type === 'trigger' ? ' by a new file' : ''}`
    case 'ChoreographyRunFinished': return `${m.choreography_name ?? 'Choreography'} run ${m.status ?? 'finished'}`
    case 'ChoreographyTriggerAdded': return `Trigger added on bucket ${m.bucket}${m.prefix ? ` (${m.prefix})` : ''}`
    case 'ChoreographyTriggerRemoved': return 'Trigger removed'
    case 'EndpointLifecycleChanged': return `${row.endpoint_id}: ${m.state}${m.reason ? ` — ${m.reason}` : ''}`
    case 'EndpointStarted': return `${row.endpoint_id} started`
    case 'EndpointStopped': return `${row.endpoint_id} stopped`
    case 'LeaderElected': return `${m.leader_id ?? row.endpoint_id} leads the endpoints`
    case 'VirtualEnvironmentRepaired': {
      const steps = (m.steps as { status: string; text: string }[] | undefined) ?? []
      const fixed = steps.filter(x => x.status === 'fixed').map(x => x.text)
      const failed = steps.filter(x => x.status === 'failed').length
      const who = m.automatic ? 'Repaired by the platform (unavailable for 10 min)' : 'Repaired'
      const retried = (m.retried_endpoints as string[] | undefined)?.length
        ? ` · tried again: ${(m.retried_endpoints as string[]).join(', ')}` : ''
      return `${who}: ${fixed.length ? fixed.join('; ') : 'everything was working'}${failed ? ` · ${failed} check${failed === 1 ? '' : 's'} failing` : ''}${retried}`
    }
    case 'VirtualEnvironmentLifecycleChanged': return `VE ${String(m.lifecycle).toLowerCase()}${m.reason ? ` — ${m.reason}` : ''}`
    case 'VirtualEnvironmentSettingsChanged': return `Settings changed${m.changes ? `: ${Object.keys(m.changes).join(', ')}` : ''}`
    default: return words(row.event_type)
  }
}
