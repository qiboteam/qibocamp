import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createRenderer, ref } from 'vue'

const tick = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms))

test('a report opens at once, its plots generated as their batch ends', async t => {
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  globalThis.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  const vite = await createServer({
    configFile: false,
    root: fileURLToPath(new URL('..', import.meta.url)),
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom'
  })
  const originalSocket = globalThis.WebSocket
  const originalFetch = globalThis.fetch
  const sockets = []
  globalThis.WebSocket = class {
    constructor(url) { this.url = url; this.sent = []; this.readyState = 0; sockets.push(this) }
    open() { this.readyState = 1; this.onopen?.() }
    send(text) { this.sent.push(JSON.parse(text)) }
    close() { this.closed = true }
    message(data) { this.onmessage({ data: JSON.stringify(data) }) }
  }
  const named = [
    { id: 'rabi-0', name: 'Rabi', status: 'success', num_figures: 1, notes: [] },
    { id: 'ramsey-0', name: 'Ramsey', status: 'pending', num_figures: 0, notes: [] },
    { id: 't1-0', name: 'T1', status: 'pending', num_figures: 0, notes: [] }
  ]
  const queries = []
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options?.body || '{}')
    queries.push(body.variables?.protocol_id || null)
    if (String(body.query).includes('protocol(report_id')) {
      return Response.json({ data: { protocol: { html: 'cached', figures: [{ id: 'f' }], error: null, error_code: null } } })
    }
    return Response.json({ data: { report: { id: 'run', has_cached_report: true, history: {}, notes: [], protocols_summary: [], protocols_detail: named } } })
  }

  try {
    const { state } = await vite.ssrLoadModule('/src/store.js')
    const { useReportDetail } = await vite.ssrLoadModule('/src/composables/useReportDetail.js')
    state.activeServer = { id: 'server', url: 'http://server.example' }
    state.auth.enabled = false
    let detail
    const renderer = createRenderer({ createComment: text => ({ text }), insert: () => {}, remove: () => {}, parentNode: () => null, nextSibling: () => null })
    const app = renderer.createApp({ setup() { detail = useReportDetail(ref('run')); return () => null } })
    app.mount({})

    await t.test('every protocol at once: the cached fetch theirs, the others are placeholders', async () => {
      await detail.loadReportData()
      assert.equal(detail.loading.value, false, 'nothing waits for the plots')
      assert.deepEqual(detail.protocols.value.map(p => p.outputs), ['pending', 'generating', 'generating'])
      const socket = sockets.at(-1)
      assert.match(socket.url, /\/ws\/reports\/run$/, 'the generation socket, opened for the placeholders')
    })

    await t.test('a placeholder on screen is generated first; each protocol fills its card', async () => {
      const socket = sockets.at(-1)
      socket.open()
      detail.requestProtocolOutputs('t1-0')
      await tick(200)
      assert.deepEqual(socket.sent.at(-1), { type: 'visible', ids: ['t1-0'] })
      socket.message({ type: 'queued', ids: ['t1-0', 'ramsey-0'], regenerating: [], generating: ['t1-0'] })
      socket.message({ type: 'protocol', protocol: { id: 't1-0', name: 'T1', status: 'success', html: 'decay', figures: [{ id: 't1' }], notes: [] } })
      const t1 = detail.protocols.value.find(p => p.id === 't1-0')
      assert.equal(t1.outputs, 'ready')
      assert.equal(t1.html, 'decay')
      assert.equal(detail.protocols.value.find(p => p.id === 'ramsey-0').outputs, 'generating', 'the others still wait')
      assert.ok(!queries.includes('t1-0'), 'no GraphQL request for a protocol not generated')
    })

    await t.test('regenerating one protocol keeps its plots, marked, until the new ones arrive', async () => {
      const socket = sockets.at(-1)
      await detail.regenerateProtocol('t1-0')
      assert.deepEqual(socket.sent.at(-1), { type: 'regenerate', ids: ['t1-0'] })
      const marked = detail.protocols.value.find(p => p.id === 't1-0')
      assert.equal(marked.regenerating, true)
      assert.equal(marked.html, 'decay', 'the previous plots stay')
      socket.message({ type: 'protocol', protocol: { id: 't1-0', name: 'T1', status: 'success', html: 'new decay', figures: [], notes: [] } })
      const redone = detail.protocols.value.find(p => p.id === 't1-0')
      assert.equal(redone.regenerating, false)
      assert.equal(redone.html, 'new decay')
    })

    await t.test('the whole report regenerates the same way, and says when it is done', async () => {
      const socket = sockets.at(-1)
      await detail.handleRegenerate()
      assert.deepEqual(socket.sent.at(-1), { type: 'regenerate', ids: null })
      assert.equal(detail.regenerating.value, true)
      assert.ok(detail.protocols.value.every(p => p.regenerating))
      socket.message({ type: 'done' })
      assert.equal(detail.regenerating.value, false)
      assert.match(detail.statusBanner.value, /regenerated/)
    })

    await t.test('the socket closing early says so, and another report closes it', async () => {
      const socket = sockets.at(-1)
      socket.onclose()
      assert.match(detail.statusBanner.value, /stopped before the end/)
      await detail.loadReportData()
      await tick(1200)
      const next = sockets.at(-1)
      assert.notEqual(next, socket)
      app.unmount()
      assert.equal(next.closed, true)
    })
  } finally {
    globalThis.WebSocket = originalSocket
    globalThis.fetch = originalFetch
    await vite.close()
  }
})
