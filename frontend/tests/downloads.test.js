import assert from 'node:assert/strict'
import { test, mock } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { createSSRApp, createRenderer, h, reactive, ssrContextKey } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createRouter, createMemoryHistory } from 'vue-router'

test('authenticated downloads and metadata previews', async t => {
  const storage = new Map()
  globalThis.localStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key)
  }
  globalThis.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  const vite = await createServer({
    configFile: false,
    root: fileURLToPath(new URL('..', import.meta.url)),
    plugins: [
      {
        name: 'plotly-test-stub',
        enforce: 'pre',
        resolveId: id => id === 'plotly.js-dist-min' ? '\0plotly-test-stub' : null,
        load: id => id === '\0plotly-test-stub' ? 'export default {}' : null
      },
      vue()
    ],
    ssr: { noExternal: ['plotly.js-dist-min'] },
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom'
  })

  try {
    const store = await vite.ssrLoadModule('/src/store.js')
    const { downloadApiFile, openApiFile, getReportDownloadFilename, readBlob } = await vite.ssrLoadModule('/src/utils/apiFiles.js')
    const { default: ApiFileButton } = await vite.ssrLoadModule('/src/components/ApiFileButton.vue')
    const { default: SidebarProtocols } = await vite.ssrLoadModule('/src/components/sidebar/SidebarProtocols.vue')
    const { default: ProtocolCard } = await vite.ssrLoadModule('/src/components/report/ProtocolCard.vue')
    const { default: PlatformView } = await vite.ssrLoadModule('/src/views/PlatformView.vue')
    const { default: ArchivesView } = await vite.ssrLoadModule('/src/views/ArchivesView.vue')
    const first = { id: 'first', url: 'https://first.example' }
    const second = { id: 'second', url: 'https://second.example' }
    const viewer = { id: 'viewer', username: 'viewer', role: 'viewer' }

    function setupBrowser(server = first, authenticated = true) {
      store.state.activeServer = server
      store.state.auth.enabled = authenticated
      store.setServerAuth(server, authenticated ? 'first-session' : null, authenticated ? viewer : null)
      store.setServerAuth(second, 'second-session', viewer)
      const links = []
      const blobs = []
      const timers = []
      const tab = { opener: {}, closed: false, location: { replace: mock.fn() }, close: mock.fn() }
      globalThis.window = {
        location: { hash: '#/reports/test' },
        open: mock.fn(() => tab),
        alert: mock.fn()
      }
      globalThis.document = {
        createElement(tag) {
          assert.equal(tag, 'a')
          const link = { click: mock.fn(), remove: mock.fn() }
          links.push(link)
          return link
        },
        body: { appendChild: mock.fn() }
      }
      mock.method(URL, 'createObjectURL', blob => {
        blobs.push(blob)
        return `blob:file-${blobs.length}`
      })
      const revoke = mock.method(URL, 'revokeObjectURL', () => {})
      mock.method(globalThis, 'setTimeout', (callback, delay) => {
        timers.push({ callback, delay })
        return timers.length
      })
      return { links, blobs, timers, tab, revoke }
    }

    async function setupComponent(component, props = {}, router = null) {
      let result
      const app = createSSRApp({
        setup() {
          result = component.setup(props, { expose() {} })
          return () => h('div')
        }
      })
      if (router) app.use(router)
      await renderToString(app)
      return result
    }

    function zipResponse(disposition = 'attachment; filename="report.zip"') {
      return new Response('zip bytes', {
        headers: { 'Content-Type': 'application/zip', 'Content-Disposition': disposition }
      })
    }

    await t.test('remote downloads send the viewer token and preserve the server filename and bytes', async () => {
      const browser = setupBrowser()
      const fetch = mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, 'https://first.example/api/reports/run/download/full')
        assert.equal(options.headers.get('Authorization'), 'Bearer first-session')
        assert.equal(options.cache, 'no-store')
        return zipResponse()
      })
      assert.equal(await downloadApiFile('/api/reports/run/download/full', 'fallback.zip'), 'report.zip')
      assert.equal(fetch.mock.callCount(), 1)
      assert.equal(browser.links[0].href, 'blob:file-1')
      assert.equal(browser.links[0].download, 'report.zip')
      assert.equal(browser.links[0].click.mock.callCount(), 1)
      assert.equal(browser.links[0].remove.mock.callCount(), 1)
      assert.equal(browser.blobs[0].type, 'application/zip')
      assert.equal(await browser.blobs[0].text(), 'zip bytes')
      assert.equal(browser.revoke.mock.callCount(), 0)
      assert.equal(browser.timers[0].delay, 60_000)
      browser.timers[0].callback()
      assert.equal(browser.revoke.mock.calls[0].arguments[0], 'blob:file-1')
      mock.restoreAll()
    })

    await t.test('a body is read as a stream, never collected by Response.blob(), which WebKitGTK cannot do for large ones', async () => {
      const browser = setupBrowser()
      const chunks = ['zip ', 'by', 'tes']
      mock.method(globalThis, 'fetch', async () => {
        const body = new ReadableStream({
          pull(controller) {
            if (chunks.length) controller.enqueue(new TextEncoder().encode(chunks.shift()))
            else controller.close()
          }
        })
        const response = new Response(body, { headers: { 'Content-Type': 'application/zip' } })
        response.blob = () => { throw new Error('Response.blob() freezes the launcher on large bodies') }
        return response
      })
      assert.equal(await downloadApiFile('/api/reports/run/download/full', 'report.zip'), 'report.zip')
      assert.equal(browser.blobs[0].type, 'application/zip')
      assert.equal(await browser.blobs[0].text(), 'zip bytes')
      await openApiFile('/api/reports/run/meta.json')
      assert.equal(browser.blobs.length, 2)
      mock.restoreAll()
    })

    await t.test('readBlob falls back to blob() where a body cannot be streamed', async () => {
      const blob = new Blob(['whole'], { type: 'text/plain' })
      assert.equal(await readBlob({ body: null, blob: async () => blob }), blob)
    })

    await t.test('the upload of a report reads the downloaded archive as a stream', async () => {
      const { readFileSync } = await import('node:fs')
      const source = readFileSync(new URL('../src/components/modals/UploadModal.vue', import.meta.url), 'utf8')
      assert.match(source, /await readBlob\(download\)/)
      assert.doesNotMatch(source, /\.blob\(\)/)
    })

    await t.test('UTF-8, unquoted, and missing filenames are handled', async () => {
      const browser = setupBrowser()
      for (const [header, expected] of [
        ["attachment; filename=ignored.zip; filename*=UTF-8''calibration%20%CE%B1.zip", 'calibration \u03b1.zip'],
        ['attachment; filename=platform.zip', 'platform.zip'],
        ['', 'fallback.zip']
      ]) {
        mock.method(globalThis, 'fetch', async () => zipResponse(header))
        assert.equal(await downloadApiFile('/file', 'fallback.zip'), expected)
        assert.equal(browser.links.at(-1).download, expected)
      }
      assert.equal(getReportDownloadFilename('nested/21:47:29_run'), '21-47-29_run.zip')
      assert.equal(getReportDownloadFilename('nested/21:47:29_run', '_new_platform'), '21-47-29_run_new_platform.zip')
      mock.restoreAll()
    })

    await t.test('local open servers download without an Authorization header', async () => {
      setupBrowser(null, false)
      mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, '/file')
        assert.equal(options.headers.has('Authorization'), false)
        return zipResponse()
      })
      await downloadApiFile('/file')
      mock.restoreAll()
    })

    await t.test('a pending request remains tied to its original server', async () => {
      const browser = setupBrowser()
      let resolve
      mock.method(globalThis, 'fetch', (url, options) => {
        assert.equal(url, 'https://first.example/file')
        assert.equal(options.headers.get('Authorization'), 'Bearer first-session')
        return new Promise(done => { resolve = done })
      })
      const download = downloadApiFile('/file')
      store.state.activeServer = second
      resolve(zipResponse())
      await download
      assert.equal(browser.links.length, 1)
      assert.equal(store.getActiveAuthToken(second), 'second-session')
      mock.restoreAll()
    })

    await t.test('HTTP and network failures never download an error response', async () => {
      const browser = setupBrowser()
      for (const [response, message] of [
        [Response.json({ detail: 'Authentication required' }, { status: 401 }), 'Authentication required'],
        [Response.json({ detail: 'Protocol data not found' }, { status: 404 }), 'Protocol data not found'],
        [new Response('Unavailable', { status: 503 }), 'Failed to fetch file (503)']
      ]) {
        mock.method(globalThis, 'fetch', async () => response)
        await assert.rejects(downloadApiFile('/file'), { message })
      }
      assert.equal(store.getActiveAuthToken(first), null)
      assert.equal(window.location.hash, '#/servers')
      mock.method(globalThis, 'fetch', async () => { throw new Error('Offline') })
      await assert.rejects(downloadApiFile('/file'), { message: 'Offline' })
      assert.equal(browser.links.length, 0)
      assert.equal(browser.blobs.length, 0)
      mock.restoreAll()
    })

    await t.test('metadata opens a detached tab synchronously and displays authenticated JSON', async () => {
      const browser = setupBrowser()
      let resolve
      mock.method(globalThis, 'fetch', (url, options) => {
        assert.equal(url, 'https://first.example/meta.json')
        assert.equal(options.headers.get('Authorization'), 'Bearer first-session')
        return new Promise(done => { resolve = done })
      })
      const preview = openApiFile('/meta.json')
      assert.deepEqual(window.open.mock.calls[0].arguments, ['about:blank', '_blank'])
      assert.equal(browser.tab.opener, null)
      assert.equal(browser.tab.location.replace.mock.callCount(), 0)
      resolve(Response.json({ platform: 'test' }))
      await preview
      assert.equal(browser.tab.location.replace.mock.calls[0].arguments[0], 'blob:file-1')
      assert.equal(browser.blobs[0].type, 'application/json')
      assert.deepEqual(JSON.parse(await browser.blobs[0].text()), { platform: 'test' })
      assert.equal(browser.links.length, 0)
      assert.equal(browser.tab.close.mock.callCount(), 0)
      browser.timers[0].callback()
      assert.equal(browser.revoke.mock.callCount(), 1)
      mock.restoreAll()
    })

    await t.test('a blocked popup shows the file over the page instead', async () => {
      setupBrowser()
      const { fileViewer, closeFile } = await vite.ssrLoadModule('/src/utils/fileViewer.js')
      mock.method(window, 'open', () => null)
      mock.method(globalThis, 'fetch', async () => Response.json({ platform: 'test' }))
      await openApiFile('/meta.json')
      assert.equal(fileViewer.open, true)
      assert.equal(fileViewer.name, 'meta.json')
      assert.equal(fileViewer.text, '{\n  "platform": "test"\n}', 'indented, to read')
      closeFile()
      mock.restoreAll()
    })

    await t.test('inside the application, where no tab opens, the file is shown over the page', async () => {
      setupBrowser()
      const { fileViewer, closeFile } = await vite.ssrLoadModule('/src/utils/fileViewer.js')
      window.qibocampDesktop = {}
      const open = mock.method(window, 'open', () => assert.fail('No tab inside the application'))
      mock.method(globalThis, 'fetch', async () => new Response('{"targets":[0,1]}', { headers: { 'Content-Type': 'application/json' } }))
      try {
        await openApiFile('/meta.json')
        assert.equal(open.mock.callCount(), 0)
        assert.equal(fileViewer.open, true)
        assert.deepEqual(JSON.parse(fileViewer.text), { targets: [0, 1] })
      } finally {
        delete window.qibocampDesktop
        closeFile()
        mock.restoreAll()
      }
    })

    await t.test('failed previews close the reserved tab and clean up any blob', async () => {
      const browser = setupBrowser()
      mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Missing metadata' }, { status: 404 }))
      await assert.rejects(openApiFile('/meta.json'), /Missing metadata/)
      assert.equal(browser.tab.close.mock.callCount(), 1)
      assert.equal(browser.blobs.length, 0)
      mock.method(globalThis, 'fetch', async () => Response.json({}))
      mock.method(browser.tab.location, 'replace', () => { throw new Error('Navigation failed') })
      await assert.rejects(openApiFile('/meta.json'), /Navigation failed/)
      assert.equal(browser.tab.close.mock.callCount(), 2)
      assert.equal(browser.revoke.mock.callCount(), 1)
      mock.restoreAll()
    })

    await t.test('closing a preview while fetching does not create a blob URL', async () => {
      const browser = setupBrowser()
      let resolve
      mock.method(globalThis, 'fetch', () => new Promise(done => { resolve = done }))
      const preview = openApiFile('/meta.json')
      browser.tab.closed = true
      resolve(Response.json({}))
      await assert.rejects(preview, /preview tab was closed/)
      assert.equal(browser.blobs.length, 0)
      mock.restoreAll()
    })

    await t.test('a browser download failure still removes the link and releases the blob', async () => {
      const browser = setupBrowser()
      const link = { click() { throw new Error('Download failed') }, remove: mock.fn() }
      mock.method(document, 'createElement', () => link)
      mock.method(globalThis, 'fetch', async () => zipResponse())
      await assert.rejects(downloadApiFile('/file'), /Download failed/)
      assert.equal(link.remove.mock.callCount(), 1)
      browser.timers[0].callback()
      assert.equal(browser.revoke.mock.callCount(), 1)
      mock.restoreAll()
    })

    await t.test('file buttons prevent duplicate requests and surface errors', async () => {
      setupBrowser()
      const button = await setupComponent(ApiFileButton, { path: '/file', filename: 'report.zip', preview: false })
      let resolve
      const fetch = mock.method(globalThis, 'fetch', () => new Promise(done => { resolve = done }))
      const download = button.handleClick()
      assert.equal(button.busy.value, true)
      await button.handleClick()
      assert.equal(fetch.mock.callCount(), 1)
      resolve(Response.json({ detail: 'Authentication required' }, { status: 401 }))
      await download
      assert.equal(button.busy.value, false)
      assert.equal(window.alert.mock.calls[0].arguments[0], 'Failed to download file: Authentication required')
      mock.restoreAll()
    })

    await t.test('expanded and collapsed sidebars and protocol cards wire all actions through file buttons', async () => {
      setupBrowser()
      const reportId = 'nested/21:47:29_run'
      const prefix = `/api/reports/${encodeURIComponent(reportId)}`
      const buttons = []
      const originalSetup = ApiFileButton.setup
      mock.method(ApiFileButton, 'setup', (props, context) => {
        const actions = originalSetup(props, context)
        buttons.push({ props, actions })
        return actions
      })
      for (const isCollapsed of [false, true]) {
        buttons.length = 0
        const html = await renderToString(createSSRApp(SidebarProtocols, { reportId, isCollapsed }))
        assert.equal((html.match(/aria-label="Copy report folder path for qq upload"/g) || []).length, 1)
        assert.deepEqual(buttons.map(button => button.props.path), [
          `${prefix}/download/full`, `${prefix}/download/new-platform`,
          `${prefix}/download/old-platform`, `${prefix}/meta.json`
        ])
        assert.deepEqual(buttons.slice(0, 3).map(button => button.props.filename), [
          '21-47-29_run.zip', '21-47-29_run_new_platform.zip', '21-47-29_run_old_platform.zip'
        ])
        assert.equal(buttons[3].props.preview, true)
        assert.equal(html.includes('first-session'), false)
        mock.method(globalThis, 'fetch', async () => Response.json({}))
        await buttons[3].actions.handleClick()
        assert.equal(window.open.mock.callCount(), isCollapsed ? 2 : 1)
      }
      buttons.length = 0
      await renderToString(createSSRApp(ProtocolCard, {
        reportId, index: 0, proto: { id: 'rabi_amplitude', name: 'Rabi', status: 'success' }
      }))
      assert.equal(buttons[0].props.path, `${prefix}/download/data/rabi_amplitude`)
      assert.equal(buttons[0].props.filename, '21-47-29_run_rabi_amplitude.zip')
      buttons.length = 0
      const html = await renderToString(createSSRApp(ProtocolCard, {
        reportId, index: 0, proto: { name: 'Pending routine', status: 'error' }
      }))
      assert.equal(buttons[0].props.path, '')
      assert.match(html, /disabled[^>]*title="Download Pending routine/)
      mock.restoreAll()
    })

    await t.test('report paths use authenticated uncached requests and copy the returned path', async () => {
      setupBrowser()
      window.isSecureContext = true
      const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
      const writeText = mock.fn()
      Object.defineProperty(globalThis, 'navigator', {
        configurable: true, value: { clipboard: { writeText } }
      })
      try {
        const sidebar = await setupComponent(SidebarProtocols, { reportId: 'nested/run' })
        for (const path of ['nested/run', '/server/reports/nested/run']) {
          mock.method(globalThis, 'fetch', async (url, options) => {
            assert.equal(url, 'https://first.example/api/reports/nested%2Frun/path')
            assert.equal(options.headers.get('Authorization'), 'Bearer first-session')
            assert.equal(options.cache, 'no-store')
            return Response.json({ path, is_absolute: path.startsWith('/') })
          })
          await sidebar.copyReportPath()
          assert.equal(writeText.mock.calls.at(-1).arguments[0], path)
          assert.equal(sidebar.pathCopied.value, true)
          assert.equal(sidebar.pathCopyTitle.value, 'Report path copied!')
          assert.equal(sidebar.copyingPath.value, false)
        }

        let resolve
        const fetch = mock.method(globalThis, 'fetch', () => new Promise(done => { resolve = done }))
        const pending = sidebar.copyReportPath()
        await sidebar.copyReportPath()
        assert.equal(fetch.mock.callCount(), 1)
        resolve(Response.json({ detail: 'Missing report' }, { status: 404 }))
        await pending
        assert.equal(sidebar.pathCopied.value, false)
        assert.equal(sidebar.copyingPath.value, false)
        assert.match(window.alert.mock.calls.at(-1).arguments[0], /Failed to copy report path/)
        assert.equal(writeText.mock.callCount(), 2)

        mock.method(globalThis, 'fetch', async () => { throw new Error('Offline') })
        await sidebar.copyReportPath()
        assert.match(window.alert.mock.calls.at(-1).arguments[0], /Offline/)

        window.isSecureContext = false
        mock.method(document, 'createElement', () => ({
          style: {}, setAttribute() {}, focus() {}, select() {}
        }))
        document.body.removeChild = mock.fn()
        document.execCommand = () => false
        mock.method(globalThis, 'fetch', async () => Response.json({ path: 'nested/run' }))
        await sidebar.copyReportPath()
        assert.equal(sidebar.pathCopied.value, false)
        assert.match(window.alert.mock.calls.at(-1).arguments[0], /Clipboard access failed/)
        document.execCommand = command => command === 'copy'
        await sidebar.copyReportPath()
        assert.equal(sidebar.pathCopied.value, true)
        assert.equal(document.body.removeChild.mock.callCount(), 2)
      } finally {
        if (originalNavigator) Object.defineProperty(globalThis, 'navigator', originalNavigator)
        else delete globalThis.navigator
        mock.restoreAll()
      }
    })

    await t.test('path copying ignores responses after report, server, session or lifecycle changes', async () => {
      const renderer = createRenderer({
        createComment: text => ({ text }),
        insert: (node, parent) => { parent.node = node },
        remove: () => {},
        parentNode: () => null,
        nextSibling: () => null
      })
      for (const change of [
        ({ props }) => { props.reportId = 'other' },
        () => { store.state.activeServer = second },
        () => { store.setServerAuth(first, null, null) },
        () => { store.state.auth.user.role = 'admin' },
        ({ app }) => app.unmount()
      ]) {
        setupBrowser()
        const props = reactive({ reportId: 'nested/run' })
        let sidebar
        const app = renderer.createApp({
          setup() {
            sidebar = SidebarProtocols.setup(props, { expose() {} })
            return () => null
          }
        })
        app.provide(ssrContextKey, { modules: new Set() })
        app.mount({})
        let resolve
        mock.method(globalThis, 'fetch', () => new Promise(done => { resolve = done }))
        const pending = sidebar.copyReportPath()
        change({ props, app })
        resolve(Response.json({ path: '/server/reports/nested/run', is_absolute: true }))
        await pending
        assert.equal(sidebar.pathCopied.value, false)
        assert.equal(sidebar.copyingPath.value, false)
        assert.equal(window.alert.mock.callCount(), 0)
        app.unmount()
        mock.restoreAll()
      }
    })

    await t.test('the platform page authenticates data requests and exposes the selected ZIP path', async () => {
      setupBrowser()
      const reportId = 'nested/run'
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: '/reports/:id/platform', name: 'platform', component: { render: () => null } }]
      })
      await router.push({ name: 'platform', params: { id: reportId }, query: { type: 'old' } })
      const page = await setupComponent(PlatformView, {}, router)
      assert.equal(page.downloadZipPath.value, '/api/reports/nested%2Frun/download/old-platform')
      mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, 'https://first.example/api/reports/nested%2Frun/platform/old')
        assert.equal(options.headers.get('Authorization'), 'Bearer first-session')
        return Response.json({ platform_name: 'Test' })
      })
      await page.loadPlatformData()
      assert.equal(page.error.value, null)
      page.activePlatformType.value = 'new'
      assert.equal(page.downloadZipPath.value, '/api/reports/nested%2Frun/download/new-platform')
      mock.restoreAll()
    })

    await t.test('archive downloads authenticate, retain filenames, and show failures instead of success', async () => {
      const browser = setupBrowser()
      const page = await setupComponent(ArchivesView)
      mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, 'https://first.example/api/archives/archive%2Fone/download')
        assert.equal(options.headers.get('Authorization'), 'Bearer first-session')
        return zipResponse('attachment; filename="Calibration.zip"')
      })
      await page.downloadArchive({ id: 'archive/one', name: 'Calibration' })
      assert.equal(browser.links[0].download, 'Calibration.zip')
      assert.equal(page.toastMessage.value, 'Downloading "Calibration.zip"...')
      mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Archive missing' }, { status: 404 }))
      await page.downloadArchive({ id: 'missing', name: 'Missing' })
      assert.equal(page.errorMessage.value, 'Archive missing')
      assert.equal(page.toastMessage.value, '')
      assert.equal(browser.links.length, 1)
      mock.restoreAll()
    })
  } finally {
    mock.restoreAll()
    await vite.close()
    delete globalThis.document
    delete globalThis.window
  }
})

test('a file reads best indented when it is JSON, and as it is otherwise', async () => {
  const { readableText } = await import('../src/utils/fileViewer.js')
  assert.equal(readableText('{"a":1}'), '{\n  "a": 1\n}')
  assert.equal(readableText('[1]', 'application/json'), '[\n  1\n]')
  assert.equal(readableText('{ broken'), '{ broken', 'not JSON after all: as it is')
  assert.equal(readableText('id: rabi\n', 'application/yaml'), 'id: rabi\n')
})
