<template>
  <div class="relative w-full">
    <input
      v-bind="$attrs"
      :value="modelValue"
      :type="visible ? 'text' : 'password'"
      class="password-input"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <button
      type="button"
      class="absolute inset-y-0 right-0 flex items-center justify-center w-8 border-0 bg-transparent text-gray-400 hover:text-gray-700 cursor-pointer"
      :aria-label="visible ? 'Hide password' : 'Show password'"
      :title="visible ? 'Hide password' : 'Show password'"
      :aria-pressed="visible"
      @click="visible = !visible"
    >
      <!-- The fields' text is 12px: an eye a little taller than it. -->
      <EyeOff v-if="visible" :size="14" aria-hidden="true" />
      <Eye v-else :size="14" aria-hidden="true" />
    </button>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  }
})

defineEmits(['update:modelValue'])

const visible = ref(false)

watch(() => props.modelValue, (value) => {
  if (!value) visible.value = false
})
</script>

<style scoped>
.password-input {
  padding-right: 2.25rem;
}
</style>
