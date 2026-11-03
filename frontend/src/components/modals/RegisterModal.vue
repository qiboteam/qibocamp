<template>
  <div
    v-if="isOpen"
    class="fixed inset-0 bg-black/40 backdrop-blur-xs z-50 flex items-center justify-center p-4 animate-fade-in"
    @click.self="close"
  >
    <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 relative border border-gray-100">
      <!-- Header -->
      <div class="flex items-center justify-between pb-3 border-b border-gray-100">
        <div class="flex items-center gap-2">
          <div class="w-7 h-7 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          </div>
          <h2 class="text-sm font-semibold text-gray-900">
            {{ isPasswordReset ? 'Reset Password' : 'Accept Invitation & Register' }}
          </h2>
        </div>
        <button
          type="button"
          @click="close"
          class="border-0 bg-transparent text-gray-400 hover:text-gray-700 text-lg leading-none cursor-pointer"
        >
          &times;
        </button>
      </div>

      <!-- Invite Validation Loading -->
      <div v-if="validating" class="py-6 text-center text-xs text-gray-500">
        <div class="w-6 h-6 border-2 border-[#833dff] border-t-transparent rounded-full animate-spin mx-auto mb-2"></div>
        Verifying token...
      </div>

      <!-- Invalid Invite Banner -->
      <div v-else-if="inviteError" class="mt-4 p-3 bg-red-50 text-red-700 text-xs rounded-xl border border-red-200">
        <div class="font-semibold mb-0.5">Invalid Token</div>
        <div>{{ inviteError }}</div>
        <div class="mt-3">
          <button
            type="button"
            @click="close"
            class="px-3 py-1.5 bg-red-100 hover:bg-red-200 text-red-800 rounded-lg text-xs font-semibold cursor-pointer transition border-0"
          >
            Dismiss
          </button>
        </div>
      </div>

      <!-- Valid Invite Form -->
      <div v-else class="mt-3">
        <!-- Invite Privileges Banner -->
        <div class="p-3 bg-purple-50/70 rounded-xl border border-purple-100 text-xs text-purple-900 mb-3 flex items-center justify-between">
          <div>
            <div class="text-[10px] text-purple-600 font-semibold uppercase tracking-wider">Role Granted</div>
            <div class="flex items-center gap-1.5 mt-0.5">
              <span
                class="px-2 py-0.5 rounded text-xs font-bold uppercase tracking-wider"
                :class="roleBadgeClass(inviteData?.role)"
              >
                {{ inviteData?.role || 'User' }}
              </span>
              <span class="text-gray-500 text-[11px]">
                {{ roleDescription(inviteData?.role) }}
              </span>
            </div>
          </div>
          <div v-if="inviteData?.expires_at" class="text-right text-[10px] text-gray-500">
            <div>Expires</div>
            <div class="font-mono text-gray-700">{{ formatExpiry(inviteData.expires_at) }}</div>
          </div>
          <div v-else class="text-right text-[10px] text-gray-500">
            <div>Expiration</div>
            <div class="font-medium text-emerald-600">Never</div>
          </div>
        </div>

        <p class="text-xs text-gray-500 mb-3">
          {{ isPasswordReset ? `Re-authenticate and restore access on ` : `Create your account on ` }}<strong class="text-gray-800">{{ serverName }}</strong>.
        </p>

        <!-- Error banner -->
        <div
          v-if="errorMessage"
          class="mb-3 p-2.5 bg-red-50 text-red-700 text-xs rounded-xl border border-red-200 flex items-center justify-between"
        >
          <span>{{ errorMessage }}</span>
          <button @click="errorMessage = ''" class="text-red-500 hover:text-red-700 text-xs font-bold border-0 bg-transparent">&times;</button>
        </div>

        <form @submit.prevent="handleRegister" class="space-y-3">
          <div>
            <label class="block text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">
              Username
            </label>
            <input
              v-model="form.username"
              type="text"
              required
              autocomplete="username"
              :readonly="!!inviteData?.target_username"
              placeholder="e.g. quantum_user"
              class="w-full px-3.5 py-2.5 rounded-xl text-xs text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
              :class="inviteData?.target_username ? 'bg-gray-200/80 text-gray-700 cursor-not-allowed' : 'bg-gray-100/70 focus:bg-white'"
            />
            <p v-if="inviteData?.target_username" class="mt-1 text-[11px] text-purple-700">
              {{ isPasswordReset ? 'Restoring credentials for' : 'This invitation is for' }} <strong>{{ inviteData.target_username }}</strong>.
            </p>
          </div>

          <div>
            <label class="block text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">
              {{ inviteData?.target_username ? 'Set / Update Password' : 'Password' }}
            </label>
            <PasswordInput
              v-model="form.password"
              required
              autocomplete="new-password"
              placeholder="At least 4 characters"
              class="w-full px-3.5 py-2.5 bg-gray-100/70 focus:bg-white rounded-xl text-xs text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
            />
          </div>

          <div>
            <label class="block text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">
              Confirm Password
            </label>
            <PasswordInput
              v-model="form.confirmPassword"
              required
              autocomplete="new-password"
              placeholder="Re-enter password"
              class="w-full px-3.5 py-2.5 bg-gray-100/70 focus:bg-white rounded-xl text-xs text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
            />
          </div>

          <div class="pt-2 flex items-center justify-between gap-2">
            <button
              type="button"
              @click="close"
              class="border-0 px-3 py-1.5 rounded-xl text-xs font-medium text-gray-600 hover:bg-gray-100 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              :disabled="submitting || !form.username.trim() || !form.password || form.password !== form.confirmPassword"
              class="border-0 px-4 py-2 rounded-xl text-xs font-medium bg-[#833dff] text-white hover:bg-[#722ce6] active:scale-[0.98] transition shadow-2xs cursor-pointer disabled:opacity-50 flex items-center gap-1.5"
            >
              <span v-if="submitting" class="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
              <span>{{ submitting ? 'Updating...' : (inviteData?.target_username ? 'Restore Access & Sign In' : 'Register & Connect') }}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { state, registerWithInvite } from '../../store.js'
import { apiGetInvite, apiGetPasswordReset } from '../../api.js'
import PasswordInput from '../PasswordInput.vue'

const router = useRouter()
const validating = ref(false)
const inviteError = ref('')
const inviteData = ref(null)
const isPasswordReset = ref(false)
const submitting = ref(false)
const errorMessage = ref('')

const form = reactive({
  username: '',
  password: '',
  confirmPassword: ''
})

const isOpen = computed(() => state.auth?.showRegisterModal)
const registerData = computed(() => state.auth?.registerData)

const serverName = computed(() => {
  return state.activeServer?.name || registerData.value?.serverUrl || 'Local Server'
})

watch(
  () => registerData.value,
  async (data) => {
    if (data?.token) {
      await validateToken(data.token, data.server)
    }
  },
  { immediate: true }
)

async function validateToken(token, server = state.activeServer) {
  validating.value = true
  inviteError.value = ''
  inviteData.value = null
  isPasswordReset.value = false
  try {
    // Try to validate as a password reset token first
    let res = await apiGetPasswordReset(token, server)
    if (res.ok) {
      const data = await res.json()
      if (data.valid) {
        inviteData.value = data
        isPasswordReset.value = true
        if (data.target_username) {
          form.username = data.target_username
        }
        return
      }
    }

    // Fall back to invite token validation
    res = await apiGetInvite(token, server)
    if (res.ok) {
      const data = await res.json()
      if (data.valid) {
        inviteData.value = data
        isPasswordReset.value = false
        if (data.target_username) {
          form.username = data.target_username
        }
      } else {
        inviteError.value = data.detail || 'Invitation link is invalid or has expired.'
      }
    } else {
      inviteError.value = 'Failed to validate invitation with the server.'
    }
  } catch (err) {
    inviteError.value = 'Could not reach server to validate invitation.'
  } finally {
    validating.value = false
  }
}

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

function roleDescription(role) {
  switch (role) {
    case 'admin':
      return 'Full administrative access'
    case 'editor':
      return 'Can modify and delete reports'
    case 'viewer':
    default:
      return 'Read-only access'
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

function close() {
  state.auth.showRegisterModal = false
  state.auth.registerData = null
  inviteError.value = ''
  errorMessage.value = ''
  form.username = ''
  form.password = ''
  form.confirmPassword = ''
}

async function handleRegister() {
  if (form.password !== form.confirmPassword) {
    errorMessage.value = 'Passwords do not match'
    return
  }
  if (!registerData.value?.token) {
    errorMessage.value = 'Missing invite token'
    return
  }

  submitting.value = true
  errorMessage.value = ''

  try {
    const targetServer = registerData.value?.server || state.activeServer
    await registerWithInvite(
      registerData.value.token,
      form.username.trim(),
      form.password,
      targetServer
    )
    close()
    router.push('/dashboard')
  } catch (err) {
    errorMessage.value = err.message || 'Registration failed'
  } finally {
    submitting.value = false
  }
}
</script>
