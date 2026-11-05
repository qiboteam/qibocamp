import { ref, watch, onUnmounted } from 'vue'
import { state, addToHistory, removeFromHistory, getActiveWsUrl, canEdit } from '../store.js'
import { REPORT_FIELDS, apiFetch, apiGraphQL } from '../api.js'
import { getPlotGenerationErrors } from '../utils/plotGeneration.js'
import { announce } from '../utils/desktop.js'
import { createLiveReportSubscription, mergeLiveProtocols, synchronizeLiveProtocols } from '../utils/liveReport.js'

/**
 * Sort protocol objects by execution order using history or stats from report detail.
 */
export function sortProtocolsByExecutionOrder(protocolsList, reportDetail) {
  if (!protocolsList || protocolsList.length <= 1) return protocolsList || []
  const history = reportDetail?.history
  const stats = reportDetail?.meta?.stats || reportDetail?.stats

  let order = []
  if (Array.isArray(history) && history.length > 0) {
    order = history
      .map(item => (typeof item === 'string' ? item : item?.id || item?.task || item?.name))
      .filter(Boolean)
  } else if (history && typeof history === 'object' && Object.keys(history).length > 0) {
    order = Object.keys(history)
  } else if (stats && typeof stats === 'object' && Object.keys(stats).length > 0) {
    order = Object.keys(stats)
  } else if (Array.isArray(reportDetail?.protocols_summary) && reportDetail.protocols_summary.length > 0) {
    order = reportDetail.protocols_summary.map(p => p.id).filter(Boolean)
  }

  if (order.length === 0) return protocolsList

  const norm = s => String(s || '').toLowerCase().replace(/[-_]/g, '').trim()
  const base = s => {
    const n = String(s || '').toLowerCase().replace(/-/g, '_').trim()
    const lastIdx = n.lastIndexOf('_')
    if (lastIdx !== -1 && /^\d+$/.test(n.slice(lastIdx + 1))) {
      return n.slice(0, lastIdx).replace(/_/g, '')
    }
    return n.replace(/_/g, '')
  }

  const usedSlots = new Set()
  const assignedSlots = new Map()

  // Pass 1: exact matches
  protocolsList.forEach((p, idx) => {
    const pid = String(p.id || '')
    for (let i = 0; i < order.length; i++) {
      if (!usedSlots.has(i) && pid === order[i]) {
        usedSlots.add(i)
        assignedSlots.set(idx, i)
        break
      }
    }
  })

  // Pass 2: normalized matches
  protocolsList.forEach((p, idx) => {
    if (assignedSlots.has(idx)) return
    const pidNorm = norm(p.id)
    for (let i = 0; i < order.length; i++) {
      if (!usedSlots.has(i) && pidNorm === norm(order[i])) {
        usedSlots.add(i)
        assignedSlots.set(idx, i)
        break
      }
    }
  })

  // Pass 3: base key matches
  protocolsList.forEach((p, idx) => {
    if (assignedSlots.has(idx)) return
    const pidBase = base(p.id)
    for (let i = 0; i < order.length; i++) {
      if (!usedSlots.has(i) && pidBase === base(order[i])) {
        usedSlots.add(i)
        assignedSlots.set(idx, i)
        break
      }
    }
  })

  return [...protocolsList].sort((a, b) => {
    const idxA = protocolsList.indexOf(a)
    const idxB = protocolsList.indexOf(b)
    const slotA = assignedSlots.has(idxA) ? assignedSlots.get(idxA) : order.length
    const slotB = assignedSlots.has(idxB) ? assignedSlots.get(idxB) : order.length
    if (slotA !== slotB) return slotA - slotB
    return idxA - idxB
  })
}

/**
 * Composable for loading report details via WebSocket streaming or HTTP fallback,
 * managing routine protocols, plot regeneration, author updates, and tag removals.
 */
/**
 * The report, with its protocols named but their outputs left out: what the
 * cards need to exist. The HTML and the figures of each come separately,
 * one protocol at a time, as its card nears the viewport.
 */
const REPORT_QUERY = `
  query Report($id: String!) {
    report(id: $id) {
      ${REPORT_FIELDS}
      history
      protocols_summary { id name category execution_time status num_figures }
      notes { content timestamp author }
      protocols_detail {
        id name category execution_time status num_figures
        notes { content timestamp author }
      }
    }
  }
`
const OUTPUTS_QUERY = `
  query Outputs($report_id: String!, $protocol_id: String!) {
    protocol(report_id: $report_id, protocol_id: $protocol_id) { html figures error error_code }
  }
`
/** The status of a protocol whose plots are not generated yet. */
const PENDING = 'pending'
/** How many placeholders on screen the server is told about: generated first. */
const GENERATION_VISIBLE = 12
/** How many protocols' outputs are in flight at once. */
const OUTPUTS_CONCURRENCY = 2

export function useReportDetail(reportId) {
  const loading = ref(true)
  const loadingStatus = ref('Connecting to server...')
  const error = ref(null)
  const report = ref(null)
  const protocols = ref([])
  const regenerating = ref(false)
  const statusBanner = ref('')
  const statusBannerError = ref(false)
  const savingAuthor = ref(false)
  const live = ref(false)
  const liveConnecting = ref(false)

  let activeWs = null
  const liveSubscription = createLiveReportSubscription({
    onState(value) {
      live.value = value === 'active'
      liveConnecting.value = value === 'connecting'
    },
    onError(message) {
      showBanner(message, 0, true)
    },
    onMessage(message) {
      if (message.type === 'metadata') {
        report.value = message.report
        addToHistory(message.report)
        protocols.value = sortProtocolsByExecutionOrder(protocols.value, message.report)
      } else if (message.type === 'snapshot') {
        protocols.value = sortProtocolsByExecutionOrder(
          synchronizeLiveProtocols(protocols.value, message.protocols),
          report.value
        )
      } else if (message.type === 'update') {
        protocols.value = sortProtocolsByExecutionOrder(
          mergeLiveProtocols(protocols.value, message.protocols, message.removed),
          report.value
        )
        const errors = getPlotGenerationErrors(message.protocols)
        if (errors.length) showBanner(`Live plot errors: ${errors.join(' ')}`, 0, true)
      }
    }
  })

  function toggleLive() {
    if (live.value || liveConnecting.value) {
      liveSubscription.stop()
    } else if (canEdit.value && !loading.value && !regenerating.value && report.value) {
      statusBanner.value = ''
      liveSubscription.start(getActiveWsUrl(
        `/ws/live/reports/${encodeURIComponent(report.value.id)}`
      ))
    }
  }

  watch(
    [
      () => typeof reportId === 'function' ? reportId() : reportId.value,
      () => state.activeServer?.id,
      () => state.activeServer?.url,
      () => state.auth.token,
      () => state.auth.user?.id,
      () => state.auth.user?.role,
      canEdit
    ],
    () => liveSubscription.stop(),
    { flush: 'sync' }
  )

  function showBanner(msg, durationMs = 3500, isError = false) {
    // Inside the launcher, the message goes where everything is said.
    if (announce(msg, isError ? 'error' : 'info')) return
    statusBanner.value = msg
    statusBannerError.value = isError
    if (durationMs > 0) {
      setTimeout(() => {
        if (statusBanner.value === msg) {
          statusBanner.value = ''
        }
      }, durationMs)
    }
  }

  function connectWebSocket(id) {
    return new Promise((resolve, reject) => {
      const encodedId = encodeURIComponent(id)
      const wsUrl = getActiveWsUrl(`/ws/reports/${encodedId}`)

      let ws = null
      try {
        ws = new WebSocket(wsUrl)
        activeWs = ws
      } catch (e) {
        reject(e)
        return
      }

      let resolved = false

      // 15-second safety timer before falling back to HTTP
      const timer = setTimeout(() => {
        if (!resolved) {
          resolved = true
          try { ws.close() } catch {}
          reject(new Error('WebSocket connection timed out'))
        }
      }, 15000)

      ws.onopen = () => {
        loadingStatus.value = 'Analyzing report directory...'
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'metadata') {
            report.value = msg.report
            state.currentReportId = msg.report.id
            addToHistory(msg.report)
            if (protocols.value?.length > 0) {
              protocols.value = sortProtocolsByExecutionOrder(protocols.value, msg.report)
            }
          } else if (msg.type === 'status') {
            loadingStatus.value = msg.message
          } else if (msg.type === 'progress') {
            loadingStatus.value = msg.message || `Plotting routine (${msg.step}/${msg.total})...`
          } else if (msg.type === 'ready') {
            clearTimeout(timer)
            resolved = true
            protocols.value = sortProtocolsByExecutionOrder(msg.protocols, report.value)
            loading.value = false
            resolve(true)
          } else if (msg.type === 'error') {
            clearTimeout(timer)
            resolved = true
            if (msg.message && msg.message.toLowerCase().includes('not found')) {
              removeFromHistory(id)
            }
            reject(new Error(msg.message || 'Report not found'))
          }
        } catch (e) {
          console.error('Failed to parse WS payload', e)
        }
      }

      ws.onerror = (e) => {
        if (!resolved) {
          clearTimeout(timer)
          resolved = true
          reject(e)
        }
      }

      ws.onclose = () => {
        if (!resolved && loading.value) {
          clearTimeout(timer)
          resolved = true
          reject(new Error('WebSocket closed before completion'))
        }
      }
    })
  }

  // --- The outputs, protocol by protocol ------------------------------------

  /** Which load the outputs belong to: a new report, or server, drops the old requests. */
  let outputsContext = 0
  const outputsQueue = { near: [], later: [], active: 0, server: null, reportId: null }

  function setProtocol(id, changes) {
    const index = protocols.value.findIndex(protocol => protocol.id === id)
    if (index < 0) return
    const next = [...protocols.value]
    next[index] = { ...next[index], ...changes }
    protocols.value = next
  }

  async function fetchOutputs(id, context) {
    setProtocol(id, { outputs: 'loading' })
    let outputs = null
    try {
      const data = await apiGraphQL(OUTPUTS_QUERY, { report_id: outputsQueue.reportId, protocol_id: id }, outputsQueue.server)
      outputs = data?.protocol || null
    } catch (err) {
      console.warn(`Outputs of ${id} could not be fetched`, err)
    }
    if (context !== outputsContext) return
    if (!outputs) {
      setProtocol(id, { outputs: 'failed' })
      return
    }
    setProtocol(id, { ...outputs, figures: outputs.figures || [], outputs: 'ready' })
  }

  function pumpOutputs(context) {
    while (outputsQueue.active < OUTPUTS_CONCURRENCY) {
      const id = outputsQueue.near.shift() ?? outputsQueue.later.shift()
      if (id === undefined) return
      const protocol = protocols.value.find(entry => entry.id === id)
      if (!protocol || protocol.outputs !== 'pending') continue
      outputsQueue.active += 1
      fetchOutputs(id, context).finally(() => {
        outputsQueue.active -= 1
        if (context !== outputsContext) return
        // The ones in view follow at once; the rest wait for an idle moment.
        if (outputsQueue.near.length) pumpOutputs(context)
        else idle(() => pumpOutputs(context))
      })
    }
  }

  function idle(callback) {
    if (typeof requestIdleCallback === 'function') requestIdleCallback(callback, { timeout: 1000 })
    else setTimeout(callback, 100)
  }

  /** A card came near: its outputs go to the head of the line. */
  function requestProtocolOutputs(id) {
    const protocol = protocols.value.find(entry => entry.id === id)
    if (protocol?.outputs === 'generating') {
      showGenerating(id)
      return
    }
    if (!protocol || protocol.outputs !== 'pending') return
    outputsQueue.later = outputsQueue.later.filter(entry => entry !== id)
    if (!outputsQueue.near.includes(id)) outputsQueue.near.push(id)
    pumpOutputs(outputsContext)
  }

  /** Everything still pending, in reading order, for the idle moments. */
  function prefetchOutputs() {
    outputsQueue.later = protocols.value.filter(entry => entry.outputs === 'pending').map(entry => entry.id)
    idle(() => pumpOutputs(outputsContext))
  }

  /**
   * The report over GraphQL, every protocol named, at once. Those generated
   * already fetch their outputs card by card; those not yet — status
   * `pending` — are placeholders the generation socket fills as their batch
   * ends. Answers 'ready' then; 'generate' for a server older than that,
   * which names no pending protocol and generates a whole report over its
   * socket before answering. A missing report is thrown.
   */
  async function loadViaGraphQL(id) {
    const data = await apiGraphQL(REPORT_QUERY, { id })
    if (!data?.report) {
      removeFromHistory(id)
      throw new Error('Report not found (404)')
    }
    const { protocols_detail: named, ...metadata } = data.report
    report.value = metadata
    state.currentReportId = metadata.id
    addToHistory(metadata)
    const pending = (named || []).filter(protocol => protocol.status === PENDING)
    // A server that names pending protocols generates them protocol by protocol.
    progressive = pending.length ? true : null
    if (!metadata.has_cached_report && !pending.length) {
      progressive = false
      return 'generate'
    }
    outputsContext += 1
    outputsQueue.near = []
    outputsQueue.later = []
    outputsQueue.server = state.activeServer
    outputsQueue.reportId = id
    protocols.value = sortProtocolsByExecutionOrder(
      (named || []).map(protocol => ({
        ...protocol,
        html: '',
        figures: [],
        outputs: protocol.status === PENDING ? 'generating' : 'pending'
      })),
      metadata
    )
    if (pending.length) openGeneration(id)
    return 'ready'
  }

  // --- The plots not generated yet, as their batch ends ----------------------

  /** Whether the server generates protocol by protocol: null until known. */
  let progressive = null

  /** The socket of the report's generation, shared with every page that has it open. */
  const generation = { socket: null, reportId: null, server: null, visible: [], timer: null, waiting: [], modern: null, decided: [] }

  function closeGeneration() {
    clearTimeout(generation.timer)
    if (generation.socket) {
      try { generation.socket.close() } catch {}
    }
    for (const resolve of generation.decided.splice(0)) resolve(false)
    Object.assign(generation, { socket: null, reportId: null, server: null, visible: [], timer: null, waiting: [], modern: null, decided: [] })
  }

  /**
   * Whether the server generates protocol by protocol: it answers `queued`
   * first, where an older one says `status` and sends the report whole.
   */
  function generationSupported(id) {
    if (progressive === false) return Promise.resolve(false)
    if (!openGeneration(id)) return Promise.resolve(false)
    if (progressive === true) return Promise.resolve(true)
    if (generation.modern !== null) return Promise.resolve(generation.modern)
    return new Promise(resolve => {
      generation.decided.push(resolve)
      setTimeout(() => resolve(false), 5000)
    })
  }

  function decideGeneration(modern) {
    if (generation.modern !== null) return
    generation.modern = modern
    for (const resolve of generation.decided.splice(0)) resolve(modern)
  }

  function sendGeneration(message) {
    const socket = generation.socket
    if (socket?.readyState === 1) socket.send(JSON.stringify(message))
    else generation.waiting.push(message)
  }

  /** Open the report's generation socket, once: what it sends fills the placeholders. */
  function openGeneration(id) {
    if (generation.socket && generation.reportId === id) return generation.socket
    closeGeneration()
    let socket
    try {
      socket = new WebSocket(getActiveWsUrl(`/ws/reports/${encodeURIComponent(id)}`))
    } catch (err) {
      console.warn('The plots cannot be generated without the report socket', err)
      return null
    }
    const server = state.activeServer
    Object.assign(generation, { socket, reportId: id, server })
    const current = () => generation.socket === socket
    socket.onopen = () => {
      if (!current()) return
      for (const message of generation.waiting.splice(0)) socket.send(JSON.stringify(message))
      if (generation.visible.length) socket.send(JSON.stringify({ type: 'visible', ids: generation.visible }))
    }
    socket.onmessage = event => {
      if (!current()) return
      let message
      try { message = JSON.parse(event.data) } catch { return }
      onGeneration(message)
    }
    socket.onclose = () => {
      if (!current()) return
      generation.socket = null
      decideGeneration(false)
      const left = protocols.value.filter(protocol => protocol.outputs === 'generating' || protocol.regenerating)
      if (left.length) showBanner('Plot generation stopped before the end: reload the report to go on.', 0, true)
      for (const protocol of left) setProtocol(protocol.id, { regenerating: false })
      if (regenerating.value) regenerating.value = false
    }
    return socket
  }

  function onGeneration(message) {
    if (message.type === 'metadata') {
      report.value = { ...report.value, ...message.report }
    } else if (['status', 'progress'].includes(message.type)) {
      decideGeneration(false)
    } else if (message.type === 'queued') {
      decideGeneration(true)
      for (const id of message.regenerating || []) {
        const protocol = protocols.value.find(entry => entry.id === id)
        if (protocol && protocol.outputs !== 'generating') setProtocol(id, { regenerating: true })
      }
    } else if (message.type === 'protocol') {
      const protocol = message.protocol
      setProtocol(protocol.id, { ...protocol, figures: protocol.figures || [], outputs: 'ready', regenerating: false })
      if (report.value && protocol.status === 'success') report.value.has_cached_report = true
    } else if (message.type === 'done') {
      if (regenerating.value) {
        regenerating.value = false
        const errors = getPlotGenerationErrors(protocols.value)
        if (errors.length) showBanner(`Some plots could not be generated: ${errors.join(' ')}`, 0, true)
        else showBanner('Report plots regenerated successfully!', 3500)
      }
    } else if (message.type === 'ready') {
      // A server that generates a report whole, then sends it.
      protocols.value = sortProtocolsByExecutionOrder(
        message.protocols.map(protocol => ({ ...protocol, outputs: 'ready' })),
        report.value
      )
      if (regenerating.value) regenerating.value = false
    } else if (message.type === 'error') {
      showBanner(message.message || 'Plot generation failed.', 0, true)
      if (regenerating.value) regenerating.value = false
    }
  }

  /** A placeholder came near: the server generates it first. */
  function showGenerating(id) {
    generation.visible = [id, ...generation.visible.filter(entry => entry !== id)].slice(0, GENERATION_VISIBLE)
    clearTimeout(generation.timer)
    generation.timer = setTimeout(() => sendGeneration({ type: 'visible', ids: generation.visible }), 150)
  }

  /**
   * Generate protocols again — all of them without `ids` — keeping their
   * plots, marked, until the new ones arrive. False when the report socket
   * cannot be opened, for the caller to regenerate another way.
   */
  async function regenerateProtocols(ids = null) {
    const id = typeof reportId === 'function' ? reportId() : reportId.value
    if (!id || !(await generationSupported(id))) return false
    for (const protocol of protocols.value) {
      if (ids === null || ids.includes(protocol.id)) setProtocol(protocol.id, { regenerating: true })
    }
    sendGeneration({ type: 'regenerate', ids })
    return true
  }

  async function regenerateProtocol(protocolId) {
    if (!canEdit.value || loading.value) return
    if (!(await regenerateProtocols([protocolId]))) {
      showBanner('This server regenerates whole reports only: use Regenerate in the sidebar.', 6000, true)
    }
  }

  /** The protocols generated in one request, where the WebSocket could not be opened. */
  async function loadProtocolsViaHttp(id) {
    try {
      loadingStatus.value = 'Retrieving report protocols...'
      const protoRes = await apiFetch(`/api/reports/${encodeURIComponent(id)}/protocols`)
      if (!protoRes.ok) throw new Error(`Failed to load protocols (${protoRes.status})`)
      protocols.value = sortProtocolsByExecutionOrder(await protoRes.json(), report.value)
    } catch (err) {
      error.value = err.message
    } finally {
      loading.value = false
    }
  }

  async function loadReportData() {
    const id = typeof reportId === 'function' ? reportId() : reportId.value
    if (!id) return
    liveSubscription.stop()

    loading.value = true
    error.value = null
    report.value = null
    protocols.value = []
    regenerating.value = false
    statusBanner.value = ''
    loadingStatus.value = 'Connecting to server...'

    if (activeWs) {
      try { activeWs.close() } catch {}
      activeWs = null
    }
    closeGeneration()
    outputsContext += 1

    // One request names the report and its protocols; a pre-cached report
    // then fetches its outputs card by card. One that has to generate its
    // plots goes through the WebSocket, which reports the progress.
    try {
      loadingStatus.value = 'Fetching report...'
      const via = await loadViaGraphQL(id)
      if (via === 'ready') {
        loading.value = false
        prefetchOutputs()
        return
      }
    } catch (err) {
      error.value = err.message
      loading.value = false
      return
    }

    try {
      const wsOk = await connectWebSocket(id)
      if (wsOk) return
    } catch (err) {
      console.warn('WebSocket streaming unavailable, falling back to HTTP:', err)
    }

    await loadProtocolsViaHttp(id)
  }

  async function handleRegenerate() {
    const id = typeof reportId === 'function' ? reportId() : reportId.value
    if (!id || regenerating.value) return
    liveSubscription.stop()
    const server = state.activeServer
    const isCurrent = () => server === state.activeServer && id === (typeof reportId === 'function' ? reportId() : reportId.value)
    regenerating.value = true
    statusBanner.value = ''
    // Protocol by protocol, the previous plots shown until replaced.
    if (await regenerateProtocols(null)) return
    try {
      const encodedId = encodeURIComponent(id)
      const res = await apiFetch(`/api/reports/${encodedId}/regenerate`, { method: 'POST' }, server)
      if (res.ok) {
        const regenData = await res.json()
        if (!isCurrent()) return
        protocols.value = sortProtocolsByExecutionOrder(regenData, report.value)
        const generationErrors = getPlotGenerationErrors(regenData)
        if (generationErrors.length) {
          showBanner(`Some plots could not be generated: ${generationErrors.join(' ')}`, 0, true)
        } else {
          showBanner('Report plots regenerated successfully!', 3500)
        }
        if (report.value) {
          report.value.has_cached_report = regenData.some(proto => proto.status === 'success')
        }
      } else {
        const data = await res.json()
        throw new Error(data.detail || `Regeneration failed (${res.status})`)
      }
    } catch (err) {
      if (isCurrent()) alert('Regeneration failed: ' + err.message)
    } finally {
      if (isCurrent()) regenerating.value = false
    }
  }

  async function handleSaveAuthor({ author, onComplete }) {
    if (!report.value) return
    savingAuthor.value = true
    try {
      const encodedId = encodeURIComponent(report.value.id)
      const res = await apiFetch(`/api/reports/${encodedId}/author`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ author })
      })
      if (res.ok) {
        report.value.author = author || 'Unknown'
        if (onComplete) onComplete()
        showBanner('Author updated successfully!')
      } else {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to update author')
      }
    } catch (err) {
      alert('Failed to update author: ' + err.message)
    } finally {
      savingAuthor.value = false
    }
  }

  async function handleRemoveTag(tag) {
    if (!report.value || !tag) return
    try {
      // In the body: a tag in the path could be `..`, which the browser collapses.
      const res = await apiFetch('/api/reports/bulk-action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'unlabel', report_ids: [report.value.id], label: tag })
      })
      if (res.ok) {
        if (Array.isArray(report.value.tags)) {
          report.value.tags = report.value.tags.filter(t => t !== tag)
        }
        showBanner(`Removed tag '${tag}'`)
      } else {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to remove tag')
      }
    } catch (err) {
      alert('Failed to remove tag: ' + err.message)
    }
  }

  onUnmounted(() => {
    liveSubscription.stop()
    closeGeneration()
    if (activeWs) {
      try { activeWs.close() } catch {}
      activeWs = null
    }
  })

  return {
    requestProtocolOutputs,
    loading,
    loadingStatus,
    error,
    report,
    protocols,
    regenerating,
    statusBanner,
    statusBannerError,
    savingAuthor,
    live,
    liveConnecting,
    toggleLive,
    loadReportData,
    handleRegenerate,
    regenerateProtocol,
    handleSaveAuthor,
    handleRemoveTag
  }
}
