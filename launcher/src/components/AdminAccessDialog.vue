<!--
  An administrator's way back into a server, without a link: pick the
  administrator — or name the first one, on a server without any — choose a
  password, and the dashboard is signed in. The launcher makes a short
  invitation in the users of the server's own machine — over SSH when it
  runs on another — and redeems it at once, against the server. Inviting
  other people is the dashboard's, in Server Administration.
-->
<template>
  <div v-if="server" class="lx-veil" @pointerdown.self="!busy && $emit('close')">
    <form class="lx-dialog" role="dialog" aria-modal="true" aria-labelledby="lx-admin-access-title" @submit.prevent="submit">
      <h2 id="lx-admin-access-title" class="text-sm font-semibold text-gray-900">Administrator access</h2>
      <p class="mt-1 text-xs text-gray-500">
        Set an administrator's password on <strong>{{ server.name || server.url }}</strong>, and sign the dashboard in as them.
        Others are invited from the dashboard's Server Administration.
      </p>

      <p v-if="loading" class="mt-4 text-xs text-gray-400" role="status">Asking the server's machine for its administrators…</p>
      <div v-else class="mt-4 space-y-3">
        <form-field v-slot="{ id }" :label="admins.length ? 'Administrator' : 'First administrator'" :help="admins.length ? '' : 'The server has no administrator yet: this one is created.'">
          <select v-if="admins.length" :id="id" v-model="username" class="lx-select w-full">
            <option v-for="admin in admins" :key="admin.id || admin.username" :value="admin.username">{{ admin.username }}</option>
          </select>
          <input v-else :id="id" v-model.trim="username" type="text" class="lx-input" placeholder="admin" spellcheck="false" autocomplete="username" autocapitalize="off" />
        </form-field>
        <form-field v-slot="{ id }" label="New password">
          <password-input :id="id" v-model="password" class="lx-input" autocomplete="new-password" />
        </form-field>
        <form-field v-slot="{ id }" label="Repeat it" :error="mismatch ? 'The two passwords differ.' : ''">
          <password-input :id="id" v-model="repeated" class="lx-input" autocomplete="new-password" />
        </form-field>
      </div>

      <p v-if="error" class="mt-3 text-xs text-red-700" role="alert">{{ error }}</p>

      <div class="mt-4 flex items-center justify-end gap-2">
        <button type="button" class="lx-action" :disabled="busy" @click="$emit('close')">Cancel</button>
        <button type="submit" class="lx-primary" :disabled="busy || loading || !ready">
          {{ busy ? 'Signing in…' : 'Set password and sign in' }}
        </button>
      </div>
    </form>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import PasswordInput from '@dashboard/components/PasswordInput.vue'
import FormField from './FormField.vue'
import { desktop } from '../bridge.js'
import { launchOptionsFor, withQibocamp } from '../state.js'
import { serverUrl, storeDashboardSession } from '../lib/servers.js'

const props = defineProps({
  /** The registered server; null while closed. */
  server: { type: Object, default: null }
})
const emit = defineEmits(['close', 'signed-in'])

const admins = ref([])
const username = ref('')
const password = ref('')
const repeated = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')

const mismatch = computed(() => Boolean(repeated.value) && repeated.value !== password.value)
const ready = computed(() => Boolean(username.value) && password.value.length > 0 && password.value === repeated.value)

/** The server's machine: where its users are, over SSH when it runs on another. */
function machineOf(server) {
  const options = launchOptionsFor(server) || {}
  return { ssh: options.ssh || null, venv: options.venv || null }
}

async function load(server) {
  loading.value = true
  error.value = ''
  try {
    const users = await withQibocamp(() => desktop.administrators(machineOf(server)))
    admins.value = (Array.isArray(users) ? users : []).filter(user => user.role === 'admin')
    username.value = admins.value[0]?.username || ''
  } catch (failure) {
    admins.value = []
    error.value = failure?.message || String(failure)
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!ready.value) return
  busy.value = true
  error.value = ''
  try {
    const url = serverUrl(props.server.url)
    // A short invitation, made where the server keeps its users, and used at once.
    const invitation = await withQibocamp(() => desktop.reissueInvite({
      username: admins.value.length ? username.value : null,
      serverUrl: url,
      expiresInHours: 1,
      ...machineOf(props.server)
    }))
    const response = await fetch(`${url}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ invite_token: invitation.token, username: username.value, password: password.value })
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(body.detail || `The server answered ${response.status}.`)
    storeDashboardSession(props.server, body.access_token, body.user)
    emit('signed-in', { server: props.server, user: body.user })
    emit('close')
  } catch (failure) {
    error.value = failure?.message || String(failure)
  } finally {
    busy.value = false
  }
}

watch(() => props.server, server => {
  password.value = ''
  repeated.value = ''
  if (server) load(server)
}, { immediate: true })
</script>
