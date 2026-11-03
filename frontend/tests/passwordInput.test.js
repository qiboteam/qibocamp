import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { createSSRApp, h, ref } from 'vue'
import { renderToString } from 'vue/server-renderer'

test('a password field hides its value until asked, and its attributes reach the input', async () => {
  const vite = await createServer({
    configFile: false,
    root: fileURLToPath(new URL('..', import.meta.url)),
    plugins: [vue()],
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom'
  })
  try {
    const { default: PasswordInput } = await vite.ssrLoadModule('/src/components/PasswordInput.vue')
    const value = ref('secret')
    const html = await renderToString(createSSRApp({
      render: () => h(PasswordInput, { modelValue: value.value, id: 'pw', class: 'lx-input', autocomplete: 'new-password', required: true })
    }))
    assert.match(html, /<input[^>]*type="password"/, 'hidden at first')
    assert.match(html, /<input[^>]*id="pw"/, 'the id is the input\'s, so its label points at it')
    assert.match(html, /<input[^>]*class="[^"]*lx-input/, 'the page\'s own class on the input')
    assert.match(html, /<input[^>]*autocomplete="new-password"/)
    assert.match(html, /aria-label="Show password"/, 'the eye button says what it does')
    assert.match(html, /<button[^>]*type="button"/, 'never submits the form')
  } finally {
    await vite.close()
  }
})
