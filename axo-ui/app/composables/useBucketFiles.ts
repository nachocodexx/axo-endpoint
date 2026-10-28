import type { PreviewFile } from 'axo-ui-kit/app/components/AxoFilePreview.vue'
import type { FileWritten } from '../utils/jobs'

interface DownloadLink { url: string; filename: string; files: number; size: number; zip: boolean }

/** Files in buckets through the Control Plane's bucket pages (your bucket
 *  access is checked; a read is billed like any download): the bytes for
 *  the preview, and downloads -- a short-lived link the browser opens
 *  itself, so a big file or a zip goes straight to disk. */
export function useBucketFiles() {
  const { apiFetch } = useApi()
  const config = useRuntimeConfig()
  const notifier = useNotifier()

  function path(f: FileWritten, inline: boolean): string {
    const q = new URLSearchParams()
    if (f.version != null) q.set('version', String(f.version))
    if (inline) q.set('inline', 'true')
    return `/buckets/${f.bucket}/objects/${encodePath(f.name)}?${q}`
  }

  const blobOf = (f: FileWritten, inline = true) => apiFetch<Blob>(path(f, inline), { responseType: 'blob' } as any)

  /** One file, or several files / folders as one zip (paths in it start at `base`). */
  async function link(bucket: string, files: { name: string; version?: number | null }[], folders: string[] = [],
    base = ''): Promise<DownloadLink> {
    const answer = await apiFetch<DownloadLink>(`/buckets/${bucket}/downloads`, {
      method: 'POST', body: { files: files.map(f => ({ name: f.name, version: f.version ?? null })), folders, base },
    })
    const a = document.createElement('a')
    a.href = `${config.public.apiBase}${answer.url}`
    a.download = answer.filename
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    return answer
  }

  async function download(f: FileWritten) {
    try {
      await link(f.bucket, [f])
    } catch (err) {
      notifier.error(err, `${f.name} couldn't be downloaded`)
    }
  }

  /** What the preview dialog needs for each file. */
  const previewOf = (files: FileWritten[]): PreviewFile[] => files.map(f => ({
    key: `${f.bucket}/${f.name}@${f.version ?? ''}`, name: f.name.split('/').pop() || f.name,
    subtitle: `${f.bucket}${f.version != null ? ` · version ${f.version}` : ''}`, size: f.size ?? null,
    load: () => blobOf(f), download: () => download(f),
  }))

  return { blobOf, link, download, previewOf }
}
