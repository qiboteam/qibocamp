<template>
  <div
    v-if="isOpen"
    class="fixed inset-0 bg-black/40 backdrop-blur-xs z-50 flex items-center justify-center p-4 animate-fade-in"
    @click.self="close"
  >
    <div class="bg-white rounded-2xl shadow-2xl max-w-sm w-full p-6 relative border border-gray-100">
      <!-- Header -->
      <div class="flex items-center justify-between pb-3 border-b border-gray-100">
        <div class="flex items-center gap-2">
          <div class="w-7 h-7 rounded-lg bg-purple-50 text-[#833dff] flex items-center justify-center">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h2 class="text-sm font-semibold text-gray-900">
            Sign In to Server
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

      <p class="text-xs text-gray-500 mt-2">
        Enter your credentials for <strong class="text-gray-800">{{ activeServer?.name || 'this server' }}</strong>.
      </p>

      <!-- Error message banner -->
      <div
        v-if="errorMessage"
        class="mt-3 p-2.5 bg-red-50 text-red-700 text-xs rounded-xl border border-red-200 flex items-center justify-between"
      >
        <span>{{ errorMessage }}</span>
        <button @click="errorMessage = ''" class="text-red-500 hover:text-red-700 text-xs font-bold border-0 bg-transparent">&times;</button>
      </div>

      <!-- Form -->
      <form @submit.prevent="handleSubmit" class="mt-4 space-y-3.5">
        <div>
          <label class="block text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">
            Username
          </label>
          <input
            v-model="username"
            type="text"
            required
            autocomplete="username"
            placeholder="Username"
            class="w-full px-3.5 py-2.5 bg-gray-100/70 focus:bg-white rounded-xl text-xs text-gray-900 border-0 focus:outline-none focus:ring-2 focus:ring-[#833dff]/25 transition shadow-2xs"
          />
        </div>

        <div>
          <label class="block text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">
            Password
          </label>
          <PasswordInput
            v-model="password"
            required
            autocomplete="current-password"
            placeholder="••••••••"
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
            :disabled="submitting || !username.trim() || !password"
            class="border-0 px-4 py-2 rounded-xl text-xs font-medium bg-[#833dff] text-white hover:bg-[#722ce6] active:scale-[0.98] transition shadow-2xs cursor-pointer disabled:opacity-50 flex items-center gap-1.5"
          >
            <span v-if="submitting" class="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
            <span>{{ submitting ? 'Signing In...' : 'Sign In' }}</span>
          </button>
        </div>
      </form>

      <!-- Registration link hint -->
      <div class="mt-4 pt-3 border-t border-gray-100 text-center text-[11px] text-gray-400">
        Have an invitation link?
        <router-link
          to="/servers"
          @click="close"
          class="text-[#833dff] hover:text-[#6b21a8] font-medium ml-1 transition"
        >
          Paste it in Server Management
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { state, loginActiveServer } from '../../store.js'
import PasswordInput from '../PasswordInput.vue'

const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorMessage = ref('')

const isOpen = computed(() => state.auth?.showLoginModal)
const activeServer = computed(() => state.activeServer)

watch(isOpen, (open) => {
  if (open) {
    username.value = ''
    password.value = ''
    errorMessage.value = ''
  }
})

function close() {
  state.auth.showLoginModal = false
  errorMessage.value = ''
  password.value = ''
}

async function handleSubmit() {
  if (!username.value.trim() || !password.value) return
  submitting.value = true
  errorMessage.value = ''

  try {
    await loginActiveServer(username.value.trim(), password.value)
    close()
    if (route.path === '/servers' || route.path === '/') {
      router.push('/dashboard')
    }
  } catch (err) {
    errorMessage.value = err.message || 'Invalid username or password'
  } finally {
    submitting.value = false
  }
}
</script>
