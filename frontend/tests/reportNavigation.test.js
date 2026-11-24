import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { parse } from 'vue/compiler-sfc'

const source = readFileSync(new URL('../src/views/ReportView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const elements = []

function walk(node, parent) {
  if (node.type === 1) elements.push({ node, parent })
  for (const child of node.children || []) walk(child, node)
}
walk(descriptor.template.ast)

function attribute(node, name) {
  return node.props.find(prop => prop.type === 6 && prop.name === name)?.value?.content
}

function directive(node, name, argument) {
  return node.props.find(prop => prop.type === 7 && prop.name === name && prop.arg?.content === argument)?.exp?.content
}

test('slideshow navigation expands to the left of its toggle within one top-bar control', () => {
  const navigations = elements.filter(({ node }) => attribute(node, 'aria-label') === 'Slideshow navigation')
  assert.equal(navigations.length, 1)
  const { node: nav, parent: transition } = navigations[0]
  assert.equal(directive(nav, 'if'), 'slideshow')
  assert.equal(transition.tag, 'Transition')
  assert.equal(attribute(transition, 'name'), 'slideshow-nav')
  assert.equal(directive(transition, 'on', 'enter'), 'sizeSlideshowNav')
  // It leaves at once: a leave animation waited on a main thread a heavy report keeps busy.
  assert.equal(directive(transition, 'on', 'before-leave'), undefined)
  assert.match(directive(transition, 'on', 'after-enter'), /removeProperty\('width'\)/)

  const { parent: unit } = elements.find(({ node }) => node === transition)
  // The toggle names the mode it switches to, so its label is bound, not static.
  const toggle = unit.children.find(node => node.type === 1 && /'Slideshow'/.test(directive(node, 'bind', 'aria-label') || ''))
  assert(toggle)
  assert(unit.children.indexOf(transition) < unit.children.indexOf(toggle))
  assert.equal(directive(toggle, 'on', 'click'), 'toggleSlideshow')
  assert.equal(directive(toggle, 'bind', 'aria-expanded'), 'slideshow')
  assert(unit.loc.source.includes('GalleryHorizontalEnd'))
  assert.match(descriptor.styles[0].content, /transform: translateX\(1rem\)/)
  assert.match(descriptor.styles[0].content, /prefers-reduced-motion: reduce/)
})

test('compact slideshow controls retain ordering, accessible names, actions, and boundary guards', () => {
  const { node: nav } = elements.find(({ node }) => attribute(node, 'aria-label') === 'Slideshow navigation')
  const row = nav.children.find(node => node.type === 1)
  const controls = row.children.filter(node => node.type === 1)
  assert.deepEqual(controls.map(node => node.tag), ['button', 'button', 'span', 'button'])
  const [previous, home, counter, next] = controls
  assert.equal(attribute(previous, 'aria-label'), 'Previous slide')
  assert.equal(directive(previous, 'on', 'click'), 'moveSlide(-1)')
  assert.equal(directive(previous, 'bind', 'disabled'), 'slideIndex === 0')
  assert.equal(attribute(home, 'aria-label'), 'Report Overview')
  assert.equal(attribute(home, 'title'), 'Report Overview (Up arrow)')
  assert.equal(directive(home, 'on', 'click'), 'selectSlide(null)')
  assert.equal(home.children.find(node => node.type === 1).tag, 'Home')
  assert.equal(attribute(counter, 'aria-live'), 'polite')
  assert(counter.loc.source.includes('slideIndex + 1'))
  assert(counter.loc.source.includes('protocols.length + 1'))
  assert.equal(attribute(next, 'aria-label'), 'Next slide')
  assert.equal(directive(next, 'on', 'click'), 'moveSlide(1)')
  assert.equal(directive(next, 'bind', 'disabled'), 'slideIndex === protocols.length')
})

test('the slideshow mounts the slide\'s card alone, every card only when printing', () => {
  const { node: card, parent: slot } = elements.find(({ node }) => node.tag === 'protocol-card')
  assert.equal(directive(slot, 'for'), '{ proto, index } in cards')
  assert.equal(directive(card, 'bind', 'index'), 'index')
  // The slot keeps the protocol's place and id; its card is mounted near the viewport only.
  assert.equal(directive(slot, 'bind', 'id'), '`proto-${proto.id}`')
  assert.equal(directive(slot, 'bind', 'ref'), 'cardWindow.slotRef(proto.id)')
  assert.equal(directive(card, 'if'), 'cardWindow.isMounted(proto.id)')
  assert.match(descriptor.scriptSetup.content, /active: \(\) => !slideshow\.value && !printing\.value/)
  // Hidden cards were mounted, and their plots drawn, all the same.
  assert.equal(directive(card, 'bind', 'class'), undefined)
  const script = descriptor.scriptSetup.content
  assert.match(script, /if \(!slideshow\.value \|\| printing\.value\) return all/)
  assert.match(script, /all\.filter\(\(\{ proto \}\) => proto\.id === selectedId\.value\)/)
  // The print button waits for every card and every plot.
  assert.match(script, /printing\.value = true[\s\S]*await setPlotPrinting\(true\)[\s\S]*window\.print\(\)/)
  assert.match(script, /addEventListener\('afterprint', onAfterPrint\)/)
})
