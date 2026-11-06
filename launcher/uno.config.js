import { defineConfig, presetUno, presetAttributify } from 'unocss'

// Mirrors frontend/uno.config.js: the launcher and the dashboard must not
// show a visible jump in style when the user moves between them.

// As there: a Lucide icon's `:size="16"` is its size, not `size-16`.
const OWN_ATTRIBUTES = [
  'placeholder', 'fill', 'opacity', 'stroke-opacity',
  'size', 'width', 'height', 'stroke', 'stroke-width'
]
const ATTRIBUTES_NOT_UTILITIES = OWN_ATTRIBUTES.flatMap(name => [name, `:${name}`, `v-bind:${name}`])

export default defineConfig({
  presets: [
    presetUno(),
    presetAttributify({ ignoreAttributes: ATTRIBUTES_NOT_UTILITIES })
  ],
  content: {
    // The dashboard sources live outside this package: scan them explicitly,
    // or the embedded pages would come up unstyled. These globs are the only
    // place where plain JavaScript modules are searched for classes; widening
    // the transform pipeline to `.js` instead would hand every dependency to
    // the extractor, single-line bundles included, and never come back.
    filesystem: ['*.html', 'src/**/*.{vue,js}', '../frontend/src/**/*.{vue,js}'],
    pipeline: {
      exclude: [/[\\/]node_modules[\\/]/, /[\\/]src-tauri[\\/]/, /[\\/]dist[\\/]/]
    }
  },
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
