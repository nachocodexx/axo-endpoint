// Buckets as the Control Plane's bucket pages answer them (a bucket belongs
// to a person or a team, never to a VE: `owner/name`).

export type BucketRole = 'reader' | 'writer' | 'owner'
export const BUCKET_ROLES: BucketRole[] = ['reader', 'writer', 'owner']

export const BUCKET_ROLE_INFO: Record<BucketRole, { title: string; text: string }> = {
  reader: { title: 'Reader', text: 'Lists, previews and downloads files.' },
  writer: { title: 'Writer', text: 'A Reader who also uploads and deletes files.' },
  owner: { title: 'Owner', text: 'A Writer who also changes the settings and who has access.' },
}

export const bucketRoleTitle = (r?: string | null) => BUCKET_ROLE_INFO[r as BucketRole]?.title ?? '—'
export const bucketRoleAtLeast = (r: string | null | undefined, needed: BucketRole) =>
  BUCKET_ROLES.indexOf(r as BucketRole) >= BUCKET_ROLES.indexOf(needed)

export interface Bucket {
  bucket_id: string
  name: string                                   // owner/name
  owner: { kind: 'user' | 'team'; id: string; name: string }
  quota_bytes: number
  aliases: string[]
  created_at: number
  created_by: string
  used_bytes?: number
  files?: number
  last_change?: number | null
  my_role: BucketRole
  relation: 'mine' | 'team' | 'shared'
  // deleted ones (the "Deleted" filter)
  deleted_at?: number
  deleted_by?: string
  purge_after?: number
}

/** Who wrote a file: a person (from the page), a job, or a choreography's bucket step. */
export interface Writer {
  kind: 'person' | 'job' | 'choreography'
  user_id?: string | null
  user_name?: string | null
  ve_id?: string | null
  job_id?: string
  function_id?: string | null
  function_version?: number | null
  object_id?: string | null
  object_method?: string | null
  choreography_run_id?: string | null
  choreography_step?: string | null
  choreography_id?: string
}

export interface FileEntry {
  kind: 'file' | 'folder'
  name: string                                   // full path; a folder's ends with /
  files?: number                                 // a folder: files right under it and deeper
  version?: number
  versions?: number
  latest_complete?: number | null
  size?: number | null
  state?: 'complete' | 'uploading' | 'failed' | string
  content_hash?: string | null
  created_at?: number
  completed_at?: number | null
  by?: Writer | null
}

/** The last part of a path ("2024/march/a.csv" -> "a.csv", "2024/" -> "2024"). */
export const baseName = (path: string) => path.replace(/\/$/, '').split('/').pop() || path
/** The folder a file is in ("raw/2026/a.csv" -> "raw/2026/"; "" at the top). */
export const folderOf = (path: string) => path.slice(0, path.lastIndexOf('/') + 1)

const FILE_ICONS: Array<[RegExp, string]> = [
  [/\.(png|jpe?g|gif|webp|svg|bmp|avif|ico)$/i, 'mdi-file-image-outline'],
  [/\.(csv|tsv|xlsx?|parquet)$/i, 'mdi-file-table-outline'],
  [/\.(json|ya?ml|toml|xml)$/i, 'mdi-code-json'],
  [/\.(py|js|ts|sh|ipynb)$/i, 'mdi-file-code-outline'],
  [/\.pdf$/i, 'mdi-file-pdf-box'],
  [/\.(mp4|webm|mov|m4v|ogv)$/i, 'mdi-file-video-outline'],
  [/\.(mp3|wav|ogg|flac|m4a)$/i, 'mdi-file-music-outline'],
  [/\.(zip|tar|gz|tgz|7z|rar)$/i, 'mdi-folder-zip-outline'],
  [/\.(txt|md|log)$/i, 'mdi-file-document-outline'],
]
/** A file's icon from its extension. */
export const fileIcon = (name: string) => FILE_ICONS.find(([re]) => re.test(name))?.[1] ?? 'mdi-file-outline'

/** A bucket's page, e.g. bucketLink('dana/sales', 'files', '2024/'). */
export function bucketLink(name: string, tab = '', folder = ''): string {
  return `/buckets/${name}${tab ? `/${tab}` : ''}${folder ? `?path=${encodeURIComponent(folder)}` : ''}`
}

/** A file's name as it goes in a URL path (each part encoded, the / kept). */
export const encodePath = (name: string) => name.split('/').map(encodeURIComponent).join('/')

const UNITS = { MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 }
export type SizeUnit = keyof typeof UNITS
/** A size limit field: bytes <-> (number, unit). */
export function splitSize(bytes: number): { value: number; unit: SizeUnit } {
  for (const unit of ['TB', 'GB', 'MB'] as SizeUnit[]) {
    if (bytes >= UNITS[unit] && bytes % UNITS[unit] === 0) return { value: bytes / UNITS[unit], unit }
  }
  return { value: Math.max(1, Math.round(bytes / UNITS.MB)), unit: 'MB' }
}
export const joinSize = (value: number, unit: SizeUnit) => Math.round(value * UNITS[unit])
export const SIZE_UNITS = Object.keys(UNITS) as SizeUnit[]

/** How much of the size limit is used, 0-100 (null when unknown). */
export function usedPct(b: Pick<Bucket, 'used_bytes' | 'quota_bytes'>): number | null {
  if (b.used_bytes == null || !b.quota_bytes) return null
  return Math.min(100, (100 * b.used_bytes) / b.quota_bytes)
}

/** Days left before a deleted bucket is removed for good. */
export const daysLeft = (purgeAfter?: number, now = Date.now() / 1000) =>
  purgeAfter ? Math.max(0, Math.ceil((purgeAfter - now) / 86400)) : 0
