import { apiFetch } from '../api.js'
import { state } from '../store.js'
import { inDesktop } from './desktop.js'
import { showFile } from './fileViewer.js'

async function fetchFile(path, server) {
  const response = await apiFetch(path, { cache: 'no-store' }, server)
  if (!response.ok) {
    const data = response.headers.get('Content-Type')?.includes('application/json')
      ? await response.json()
      : null
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Failed to fetch file (${response.status})`)
  }
  return response
}

/**
 * A response's body as a Blob, read chunk by chunk.
 *
 * WebKitGTK, the desktop launcher's engine, spins the page's thread without
 * end when `Response.blob()` collects a large body — a 177 MB report archive
 * never came back — while reading the same body as a stream keeps the page
 * responsive throughout. Where the body cannot be streamed, `blob()` it is.
 */
export async function readBlob(response) {
  const reader = response.body?.getReader?.()
  if (!reader) return response.blob()
  const parts = []
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(value)
  }
  return new Blob(parts, { type: response.headers.get('Content-Type') || '' })
}

function getFilename(response, fallback) {
  const disposition = response.headers.get('Content-Disposition') || ''
  const encoded = disposition.match(/(?:^|;)\s*filename\*=utf-8''([^;]+)/i)
  if (encoded) return decodeURIComponent(encoded[1].trim())
  const plain = disposition.match(/(?:^|;)\s*filename=(?:"([^"]*)"|([^;]*))/i)
  return plain?.[1] || plain?.[2]?.trim() || fallback
}

function releaseUrlLater(url) {
  // Navigation and downloads consume blob URLs asynchronously.
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function getReportDownloadFilename(reportId, suffix = '') {
  const name = (reportId || 'report').split('/').pop().replaceAll(':', '-')
  return `${name}${suffix}.zip`
}

export async function downloadApiFile(path, filename = 'download.zip', server = state.activeServer) {
  const response = await fetchFile(path, server)
  const name = getFilename(response, filename)
  const url = URL.createObjectURL(await readBlob(response))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  try {
    document.body.appendChild(link)
    link.click()
  } finally {
    link.remove()
    releaseUrlLater(url)
  }
  return name
}

/** A file shown over the page: where no tab can open, inside the application or past a pop-up blocker. */
async function viewApiFile(path, name, server) {
  const response = await fetchFile(path, server)
  showFile(getFilename(response, name), await response.text(), response.headers.get('Content-Type') || '')
}

/**
 * Show a file: in a tab of its own in a browser; over the page inside the
 * desktop application, which opens no tab, or when the browser refuses one.
 */
export async function openApiFile(path, server = state.activeServer, name = path.split('/').pop() || 'file') {
  if (inDesktop()) return viewApiFile(path, name, server)
  // Reserve the tab during the click, before awaiting a network request.
  const tab = window.open('about:blank', '_blank')
  if (!tab) return viewApiFile(path, name, server)
  tab.opener = null
  let url
  try {
    const response = await fetchFile(path, server)
    const blob = await readBlob(response)
    if (tab.closed) throw new Error('The file preview tab was closed.')
    url = URL.createObjectURL(blob)
    tab.location.replace(url)
    releaseUrlLater(url)
  } catch (err) {
    if (url) URL.revokeObjectURL(url)
    tab.close()
    throw err
  }
}
