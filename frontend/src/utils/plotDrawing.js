/**
 * When the plots of a page are drawn, and when they are let go.
 *
 * A plot is drawn when it comes near the viewport, the nearest first and one
 * per frame, and released — its graph purged, its place kept — once it is
 * far from it: what is drawn stays a screenful or two, however long the
 * report. Drawing everything in the idle moments instead made a report of a
 * hundred protocols draw a hundred plots, and keep them, to show one: about
 * 100 000 elements, which WebKitGTK, the launcher's engine, took half a
 * minute of a blocked page to draw.
 *
 * Printing wants every plot, drawn or not: from `beforeprint` (or
 * `setPrinting(true)`) every plot is drawn at once, and from `afterprint`
 * those far from the viewport are released again.
 *
 * A viewer registers its element with `{ draw, release }`: `draw()` draws or
 * redraws it, and may return a promise; `release()` purges it.
 */

/** How far ahead of the viewport a plot is drawn. */
export const NEAR = '800px'
/** How far from the viewport a drawn plot is released: further than NEAR, so a plot at the edge is not drawn and released by turns. */
export const FAR = '2400px'

const viewers = new Map()
const queue = new Set()
let draining = false
let printing = false
/** The observers of each scroll container, the viewport's under `null`. */
const observersByRoot = new Map()
let printListening = false

/**
 * The element's scroll container, or null for the viewport. An observer of
 * the viewport sees nothing beyond a scroll container's edge, whatever its
 * margin — and the report scrolls in its panel, not in the window — so
 * "near" is measured from the container the element scrolls in.
 */
export function scrollRoot(element) {
  if (typeof getComputedStyle !== 'function') return null
  for (let node = element?.parentElement; node; node = node.parentElement) {
    const { overflowX, overflowY } = getComputedStyle(node)
    if (/(auto|scroll)/.test(`${overflowY} ${overflowX}`)) return node
  }
  return null
}

function observers(root) {
  if (typeof IntersectionObserver === 'undefined') return null
  if (!observersByRoot.has(root)) {
    const nearObserver = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const viewer = viewers.get(entry.target)
        if (!viewer) continue
        viewer.near = entry.isIntersecting
        if (viewer.near && !viewer.drawn) queue.add(viewer)
        else queue.delete(viewer)
      }
      drain()
    }, { root, rootMargin: NEAR })
    const farObserver = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const viewer = viewers.get(entry.target)
        if (!viewer) continue
        viewer.far = !entry.isIntersecting
        if (viewer.far && !printing) release(viewer)
      }
    }, { root, rootMargin: FAR })
    observersByRoot.set(root, { near: nearObserver, far: farObserver })
  }
  return observersByRoot.get(root)
}

function listenForPrinting() {
  if (printListening || typeof window === 'undefined' || !window.addEventListener) return
  printListening = true
  window.addEventListener('beforeprint', () => setPrinting(true))
  window.addEventListener('afterprint', () => setPrinting(false))
}

async function draw(viewer) {
  queue.delete(viewer)
  if (!viewers.has(viewer.element)) return
  viewer.drawn = true
  try {
    await viewer.draw()
  } catch (error) {
    console.error('A plot could not be drawn', error)
  }
}

function release(viewer) {
  queue.delete(viewer)
  if (!viewer.drawn) return
  viewer.drawn = false
  viewer.release()
}

function nextFrame() {
  return new Promise(resolve => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
    else setTimeout(resolve, 16)
  })
}

/** Draw what is near, one plot per frame, so that the page answers in between. */
async function drain() {
  if (draining) return
  draining = true
  try {
    while (queue.size) {
      await draw(queue.values().next().value)
      await nextFrame()
    }
  } finally {
    draining = false
  }
}

/** Draw `element`'s plot when it comes near the viewport: at once where nothing tells. */
export function registerPlot(element, { draw: drawPlot, release: releasePlot }) {
  if (!element) return
  unregisterPlot(element)
  const io = observers(scrollRoot(element))
  const viewer = { element, io, draw: drawPlot, release: releasePlot, drawn: false, near: false, far: true }
  viewers.set(element, viewer)
  listenForPrinting()
  if (printing || !io) {
    draw(viewer)
    return
  }
  io.near.observe(element)
  io.far.observe(element)
}

export function unregisterPlot(element) {
  const viewer = element && viewers.get(element)
  if (!viewer) return
  queue.delete(viewer)
  viewers.delete(element)
  viewer.io?.near.unobserve(element)
  viewer.io?.far.unobserve(element)
}

/** A plot whose figure changed: redrawn now if drawn, otherwise when its turn comes. */
export function redrawPlot(element) {
  const viewer = element && viewers.get(element)
  if (!viewer) return
  if (viewer.drawn || printing) draw(viewer)
  else if (viewer.near) {
    queue.add(viewer)
    drain()
  }
}

/**
 * Printing: every plot drawn, now, and none released until it ends; then
 * those far from the viewport are released. Resolves once every plot is drawn.
 */
export function setPrinting(value) {
  printing = Boolean(value)
  if (printing) {
    return Promise.all([...viewers.values()].filter(viewer => !viewer.drawn).map(draw))
  }
  for (const viewer of viewers.values()) {
    if (viewer.far) release(viewer)
  }
  return Promise.resolve()
}

export function isPrinting() {
  return printing
}

/** How many plots are drawn now, and how many are known: for the tests. */
export function plotCounts() {
  const all = [...viewers.values()]
  return { registered: all.length, drawn: all.filter(viewer => viewer.drawn).length }
}
