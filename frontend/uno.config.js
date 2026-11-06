import { defineConfig, presetUno, presetAttributify } from 'unocss'

// Attributes that are an element's own, not utilities: attributify would read
// a Lucide icon's `:size="16"` as `size-16`, 4rem square, over the icon's own
// width and height. The preset's defaults first; launcher/uno.config.js keeps
// the same list. Bound forms too: the preset compares the name before
// removing `:` or `v-bind:`.
const OWN_ATTRIBUTES = [
  'placeholder', 'fill', 'opacity', 'stroke-opacity',
  'size', 'width', 'height', 'stroke', 'stroke-width'
]
export const ATTRIBUTES_NOT_UTILITIES = OWN_ATTRIBUTES.flatMap(name => [name, `:${name}`, `v-bind:${name}`])

export default defineConfig({
  presets: [
    presetUno(),
    presetAttributify({ ignoreAttributes: ATTRIBUTES_NOT_UTILITIES })
  ],
  theme: {
    duration: {
      DEFAULT: '50ms',
      75: '25ms',
      100: '33ms',
      150: '50ms',
      200: '67ms',
      300: '100ms',
      500: '167ms',
      700: '233ms',
      1000: '333ms'
    },
    animation: {
      durations: {
        'fade-in': '0.33s'
      }
    },
    colors: {
      pale: '#f7f7f7',
      accentLight: '#ebe0ff',
      accentMild: '#c8a8ff',
      accent: '#833dff',
      note: '#4a4a4a'
    }
  }
})
