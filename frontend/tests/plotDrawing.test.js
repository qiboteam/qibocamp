import assert from 'node:assert/strict'
import { test } from 'node:test'

// What the page's observers would say, said by the test: `show(element, near, far)`.
const observers = []
globalThis.IntersectionObserver = class {
  constructor(callback, options) {
    this.callback = callback
    this.margin = options.rootMargin
    this.root = options.root
    this.elements = new Set()
    observers.push(this)
  }
  observe(element) { this.elements.add(element) }
  unobserve(element) { this.elements.delete(element) }
}
globalThis.requestAnimationFrame = callback => setTimeout(callback, 0)
const listeners = {}
globalThis.window = { addEventListener: (name, callback) => { listeners[name] = callback } }

const { FAR, NEAR, plotCounts, redrawPlot, registerPlot, setPrinting, unregisterPlot } = await import('../src/utils/plotDrawing.js')

const settle = () => new Promise(resolve => setTimeout(resolve, 20))

/** Near: within NEAR of the viewport. Far: beyond FAR. */
function show(element, { near, far }) {
  for (const observer of observers) {
    if (!observer.elements.has(element)) continue
    const intersecting = observer.margin === NEAR ? near : !far
    observer.callback([{ target: element, isIntersecting: intersecting }])
  }
}

function plot(name, log) {
  const element = { name }
  registerPlot(element, {
    draw: async () => { log.push(`draw ${name}`) },
    release: () => { log.push(`release ${name}`) }
  })
  return element
}

test('a plot is drawn near the viewport, and only there: nothing is drawn ahead in idle moments', async () => {
  const log = []
  const elements = Array.from({ length: 100 }, (_, index) => plot(`p${index}`, log))
  await settle()
  assert.deepEqual(log, [], 'nothing drawn before the page says what is near')
  show(elements[0], { near: true, far: false })
  show(elements[1], { near: true, far: false })
  for (const element of elements.slice(2)) show(element, { near: false, far: true })
  await settle()
  assert.deepEqual(log, ['draw p0', 'draw p1'])
  assert.deepEqual(plotCounts(), { registered: 100, drawn: 2 })
  elements.forEach(unregisterPlot)
})

test('a plot far from the viewport is released, and drawn again when it comes back', async () => {
  const log = []
  const element = plot('a', log)
  show(element, { near: true, far: false })
  await settle()
  // Between NEAR and FAR it stays drawn: no drawing and releasing by turns at the edge.
  show(element, { near: false, far: false })
  await settle()
  assert.deepEqual(log, ['draw a'])
  show(element, { near: false, far: true })
  assert.deepEqual(log, ['draw a', 'release a'])
  show(element, { near: true, far: false })
  await settle()
  assert.deepEqual(log, ['draw a', 'release a', 'draw a'])
  unregisterPlot(element)
  assert.ok(Number.parseInt(FAR) > Number.parseInt(NEAR))
})

test('printing draws every plot and releases none until it ends; then the far ones go', async () => {
  const log = []
  const near = plot('near', log)
  const far = plot('far', log)
  show(near, { near: true, far: false })
  show(far, { near: false, far: true })
  await settle()
  assert.deepEqual(log, ['draw near'])
  await setPrinting(true)
  assert.deepEqual(log, ['draw near', 'draw far'])
  // A card mounted for printing draws its plot at once.
  const mounted = plot('mounted', log)
  await settle()
  assert.ok(log.includes('draw mounted'))
  show(far, { near: false, far: true })
  assert.equal(log.includes('release far'), false, 'nothing released while printing')
  listeners.afterprint()
  assert.ok(log.includes('release far'))
  assert.equal(log.includes('release near'), false)
  ;[near, far, mounted].forEach(unregisterPlot)
})

test('the browser\'s own print draws everything too', async () => {
  const log = []
  const element = plot('menu', log)
  show(element, { near: false, far: true })
  listeners.beforeprint()
  await settle()
  assert.deepEqual(log, ['draw menu'])
  listeners.afterprint()
  assert.deepEqual(log, ['draw menu', 'release menu'])
  unregisterPlot(element)
})

test('a changed figure is redrawn when drawn, and waits for its turn otherwise', async () => {
  const log = []
  const drawn = plot('drawn', log)
  const away = plot('away', log)
  show(drawn, { near: true, far: false })
  show(away, { near: false, far: true })
  await settle()
  redrawPlot(drawn)
  redrawPlot(away)
  await settle()
  assert.deepEqual(log, ['draw drawn', 'draw drawn'])
  ;[drawn, away].forEach(unregisterPlot)
})

test('an unregistered plot is neither drawn nor released', async () => {
  const log = []
  const element = plot('gone', log)
  unregisterPlot(element)
  show(element, { near: true, far: false })
  await settle()
  assert.deepEqual(log, [])
  assert.deepEqual(plotCounts(), { registered: 0, drawn: 0 })
})

test('near is measured from the container the plot scrolls in, not from the viewport', async () => {
  const { scrollRoot } = await import('../src/utils/plotDrawing.js')
  const styles = new Map()
  globalThis.getComputedStyle = node => styles.get(node) || { overflowX: 'visible', overflowY: 'visible' }
  const panel = { parentElement: null }
  const card = { parentElement: panel }
  const element = { parentElement: card }
  styles.set(panel, { overflowX: 'hidden', overflowY: 'auto' })
  // A clipping card does not scroll: its plots would always count as near.
  styles.set(card, { overflowX: 'hidden', overflowY: 'hidden' })
  assert.equal(scrollRoot(element), panel)
  const before = observers.length
  registerPlot(element, { draw: async () => {}, release: () => {} })
  const created = observers.slice(before)
  assert.equal(created.length, 2)
  assert.ok(created.every(observer => observer.root === panel))
  unregisterPlot(element)
  styles.clear()
  assert.equal(scrollRoot(element), null)
  delete globalThis.getComputedStyle
})
