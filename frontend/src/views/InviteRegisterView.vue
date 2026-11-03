<template>
  <div class="min-h-screen bg-[#f7f7f7] flex flex-col justify-center py-12 px-4 sm:px-6 lg:px-8">
    <div class="sm:mx-auto sm:w-full sm:max-w-md text-center mb-6">
      <div class="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-purple-100 text-[#833dff] mb-3 shadow-2xs">
        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
        </svg>
      </div>
      <h1 class="text-2xl font-bold text-gray-900 tracking-tight">
        {{ resetting ? 'Restore Access' : 'Join Qibocamp Server' }}
      </h1>
      <p class="mt-1.5 text-xs sm:text-sm text-gray-500">
        {{ resetting && inviteInfo?.target_username ? `Re-authenticate and restore access for ${inviteInfo.target_username}` : (resetting ? 'Choose a new password for your account.' : "You've been invited to access this report instance.") }}
      </p>
    </div>

    <div class="sm:mx-auto sm:w-full sm:max-w-md">
      <div class="bg-white py-8 px-6 shadow-xl rounded-2xl border border-gray-100 sm:px-10">
        <!-- Token Validating State -->
        <div v-if="validating" class="py-10 text-center text-xs text-gray-500">
          <div class="w-8 h-8 border-3 border-[#833dff] border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
          Verifying invitation token...
        </div>

        <!-- Token Invalid Error -->
        <div v-else-if="tokenError" class="py-4 text-center">
          <div class="w-10 h-10 rounded-full bg-red-100 text-red-600 flex items-center justify-center mx-auto mb-3">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h2 class="text-sm font-bold text-gray-900 mb-1">Invitation Link Not Valid</h2>
          <p class="text-xs text-red-600 mb-5 leading-relaxed">{{ tokenError }}</p>
          <div class="flex items-center justify-center gap-3">
            <router-link
              to="/servers"
              class="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-xl text-xs font-semibold transition"
            >
              Server Management
            </router-link>
            <router-link
              to="/docs"
              class="px-4 py-2 bg-[#833dff] hover:bg-[#722ce6] text-white rounded-xl text-xs font-semibold transition shadow-2xs"
            >
              Documentation
            </router-link>
          </div>
        </div>

        <!-- Valid Token Form -->
        <div v-else>
          <!-- Server & Role Badge Card -->
          <div class="p-3.5 bg-purple-50/70 rounded-xl border border-purple-100 text-xs mb-5">
            <div class="flex items-center justify-between mb-2 pb-2 border-b border-purple-100/60">
              <span class="text-gray-500 text-[11px] font-medium uppercase tracking-wider">Server</span>
              <span class="font-semibold text-gray-900 truncate max-w-[200px]" :title="targetServer?.url">
                {{ targetServer?.name || targetServer?.url || 'Local Instance' }}
              </span>
            </div>
            <div class="flex items-center justify-between">
              <div>
                <span class="text-gray-500 text-[11px] font-medium uppercase tracking-wider block mb-0.5">{{ resetting ? 'Role' : 'Role Assigned' }}</span>
                <span
                  class="px-2 py-0.5 rounded text-xs font-bold uppercase tracking-wider inline-block"
                  :class="roleBadgeClass(inviteInfo?.role)"
                >
                  {{ inviteInfo?.role || 'Viewer' }}
                </span>
              </div>
              <div class="text-right">
                <span class="text-gray-500 text-[11px] font-medium uppercase tracking-wider block mb-0.5">Expires</span>
                <span class="text-[11px] font-mono" :class="inviteInfo?.expires_at ? 'text-gray-700' : 'text-emerald-600 font-semibold'">
                  {{ inviteInfo?.expires_at ? formatExpiry(inviteInfo.expires_at) : 'Never' }}
                </span>
              </div>
            </div>
          </div>

          <!-- Submission Error -->
          <div
            v-if="submitError"
            class="mb-4 p-3 bg-red-50 text-red-700 text-xs rounded-xl border border-red-200 flex items-center justify-between"
          >
            <span>{{ submitError }}</span>
            <button @click="submitError = ''" class="text-red-500 hover:text-red-700 text-sm font-bold border-0 bg-transparent">&times;</button>
          </div>

          <form @submit.prevent="handleRegister" class="space-y-4">
            <div>
              <label class="block text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-1.5">
                {{ inviteInfo?.target_username ? 'Username' : 'Choose Username' }}
              </label>
              <input
                v-model="username"
                type="text"
                required
                autocomplete="username"
                :readonly="!!inviteInfo?.target_username"
                placeholder="e.g. quantum_researcher"
                class="w-full px-3.5 py-2.5 rounded-xl text-xs sm:text-sm text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
                :class="inviteInfo?.target_username ? 'bg-gray-200/80 text-gray-700 cursor-not-allowed' : 'bg-gray-100/70 focus:bg-white'"
              />
              <p v-if="inviteInfo?.target_username" class="mt-1 text-[11px] text-purple-700">
                <template v-if="resetting">This link restores access for <strong>{{ inviteInfo.target_username }}</strong> ({{ inviteInfo.role || 'viewer' }}).</template>
                <template v-else>This invitation is for <strong>{{ inviteInfo.target_username }}</strong>.</template>
              </p>
            </div>

            <div>
              <label class="block text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-1.5">
                {{ inviteInfo?.target_username ? 'Set / Update Password' : 'Password' }}
              </label>
              <PasswordInput
                v-model="password"
                required
                autocomplete="new-password"
                placeholder="Choose or confirm your password"
                class="w-full px-3.5 py-2.5 bg-gray-100/70 focus:bg-white rounded-xl text-xs sm:text-sm text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
              />
            </div>

            <div>
              <label class="block text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-1.5">
                Confirm Password
              </label>
              <PasswordInput
                v-model="confirmPassword"
                required
                autocomplete="new-password"
                placeholder="Re-enter password"
                class="w-full px-3.5 py-2.5 bg-gray-100/70 focus:bg-white rounded-xl text-xs sm:text-sm text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
              />
            </div>

            <button
              type="submit"
              :disabled="submitting || !username.trim() || !password || password !== confirmPassword"
              class="w-full mt-2 px-4 py-3 rounded-xl text-xs sm:text-sm font-semibold bg-[#833dff] text-white hover:bg-[#722ce6] active:scale-[0.98] transition shadow-md cursor-pointer disabled:opacity-50 flex items-center justify-center gap-2 border-0"
            >
              <span v-if="submitting" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
              <span>{{ submitting ? 'Updating Account...' : (resetting ? 'Restore Access & Sign In' : 'Complete Registration') }}</span>
            </button>
          </form>

          <div class="mt-6 pt-4 border-t border-gray-100 text-center">
            <router-link
              to="/servers"
              class="text-xs text-gray-400 hover:text-gray-600 transition"
            >
              Cancel and return to servers
            </router-link>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { state, registerWithInvite, setActiveServer } from '../store.js'
import { apiGetInvite, apiGetPasswordReset } from '../api.js'
import { normalizeUrl } from '../utils/url.js'
import PasswordInput from '../components/PasswordInput.vue'

const route = useRoute()
const router = useRouter()

const token = ref('')
const validating = ref(true)
const tokenError = ref('')
const inviteInfo = ref(null)
const targetServer = ref(null)

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const submitError = ref('')

function roleBadgeClass(role) {
  switch (role) {
    case 'admin':
      return 'bg-purple-100 text-[#833dff]'
    case 'editor':
      return 'bg-blue-100 text-blue-800'
    case 'viewer':
    default:
      return 'bg-gray-100 text-gray-700'
  }
}

function formatExpiry(isoString) {
  try {
    const date = new Date(isoString)
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return isoString
  }
}

/** Whether the link is a password reset rather than an invitation. */
const resetting = ref(false)

async function resolveAndValidate() {
  validating.value = true
  tokenError.value = ''

  // An invitation, or a password reset: the same page, the same registration.
  resetting.value = !route.query.token && Boolean(route.query.reset_token)
  token.value = (route.query.token || route.query.reset_token || '').toString().trim()
  if (!token.value) {
    tokenError.value = 'No invitation token found in link URL.'
    validating.value = false
    return
  }

  // The server the invitation is for, named by the link: no server serves
  // this page itself, so its origin says nothing.
  const serverParam = route.query.server ? route.query.server.toString().trim() : null
  if (!serverParam) {
    tokenError.value = 'The invitation names no server: ask for a new link.'
    validating.value = false
    return
  }

  // Asked at its address until the invitation is accepted, and only then registered.
  const norm = normalizeUrl(serverParam)
  targetServer.value = state.servers.find(s => normalizeUrl(s.url) === norm) || { url: norm }

  try {
    const res = resetting.value
      ? await apiGetPasswordReset(token.value, targetServer.value)
      : await apiGetInvite(token.value, targetServer.value)
    if (res.ok) {
      const data = await res.json()
      if (data.valid) {
        inviteInfo.value = data
        if (data.target_username) {
          username.value = data.target_username
        }
      } else {
        tokenError.value = data.detail || 'Invitation link has expired or has already been used.'
      }
    } else {
      const err = await res.json().catch(() => ({}))
      tokenError.value = err.detail || 'Failed to validate invitation token.'
    }
  } catch (err) {
    tokenError.value = 'Could not reach server to validate invitation link.'
  } finally {
    validating.value = false
  }
}

async function handleRegister() {
  if (password.value !== confirmPassword.value) {
    submitError.value = 'Passwords do not match.'
    return
  }
  if (!username.value.trim() || !password.value) {
    submitError.value = 'Username and password are required.'
    return
  }

  submitting.value = true
  submitError.value = ''

  try {
    const { server } = await registerWithInvite(
      token.value,
      username.value.trim(),
      password.value,
      targetServer.value
    )
    await setActiveServer(server)
    router.push('/dashboard')
  } catch (err) {
    submitError.value = err.message || 'Registration failed.'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  resolveAndValidate()
})
</script>
