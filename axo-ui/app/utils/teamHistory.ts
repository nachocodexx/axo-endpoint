import { teamRoleTitle } from './teams'

// One line per entry of a team's history (GET /teams/{id}/history: the
// CP's own record of who did what, newest first).
export interface TeamHistoryRow {
  type: string
  at: number
  by?: string
  by_name?: string | null
  user_id_name?: string | null
  from_name?: string | null
  to_name?: string | null
  [key: string]: any
}

const ICONS: Record<string, string> = {
  TeamCreated: 'mdi-account-group-outline', TeamMemberAdded: 'mdi-account-plus-outline',
  TeamMemberRemoved: 'mdi-account-minus-outline', TeamRoleChanged: 'mdi-account-switch-outline',
  TeamUpdated: 'mdi-pencil-outline', TeamTransferred: 'mdi-swap-horizontal', TeamDeleted: 'mdi-delete-outline',
  InvitationSent: 'mdi-email-fast-outline', InvitationAnswered: 'mdi-email-check-outline',
}
export const teamHistoryIcon = (r: TeamHistoryRow) => ICONS[r.type] ?? 'mdi-circle-small'

export function teamHistoryText(r: TeamHistoryRow): string {
  const by = r.by_name ?? 'Someone'
  const who = r.user_id_name ?? 'someone'
  switch (r.type) {
    case 'TeamCreated': return `${by} created the team`
    case 'TeamMemberAdded': return `${who} joined as ${teamRoleTitle(r.role)}`
    case 'TeamMemberRemoved': return r.by === r.user_id ? `${who} left the team` : `${by} removed ${who}`
    case 'TeamRoleChanged': return `${by} made ${who} ${teamRoleTitle(r.role)}`
    case 'TeamTransferred': return `${r.from_name ?? 'The owner'} handed the team over to ${r.to_name ?? 'a member'}`
    case 'TeamDeleted': return `${by} deleted the team`
    case 'InvitationSent': return `${by} invited ${r.invitee?.name ?? 'someone'} as ${teamRoleTitle(r.role)}`
    case 'InvitationAnswered': return `${by} ${r.status === 'accepted' ? 'accepted' : r.status === 'rejected' ? 'declined' : r.status} an invitation`
    case 'TeamUpdated': {
      const c = r.changes ?? {}
      const parts: string[] = []
      if ('description' in c) parts.push('the description')
      if ('limits.per_admin_cents' in c || 'limits.team_cents' in c) parts.push('the spending limits')
      return `${by} changed ${parts.join(' and ') || 'the team'}`
    }
    default: return r.type
  }
}
