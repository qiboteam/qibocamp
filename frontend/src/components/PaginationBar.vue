<template>
  <div
    class="mt-5 flex flex-col sm:flex-row items-center justify-between gap-4 bg-white px-4 py-3 rounded-2xl border border-gray-200 shadow-2xs text-xs select-none"
  >
    <!-- Left: Range display & Page size selector -->
    <div class="flex items-center gap-3 text-gray-500 flex-wrap">
      <span>
        Showing
        <strong class="font-semibold text-gray-800">{{ paginationRange.start }}</strong>
        to
        <strong class="font-semibold text-gray-800">{{ paginationRange.end }}</strong>
        of
        <strong class="font-semibold text-gray-800">{{ totalReports }}</strong>
        {{ noun }}
      </span>
      <span class="text-gray-300">|</span>
      <div class="flex items-center gap-1.5">
        <span class="text-gray-400">Show:</span>
        <select
          :value="pageSize"
          @change="$emit('update:pageSize', Number($event.target.value))"
          class="px-2 py-1 bg-gray-50 border-0 rounded-lg text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-[#833dff] cursor-pointer"
        >
          <option v-for="size in sizes" :key="size" :value="size">{{ size }} / page</option>
        </select>
      </div>
    </div>

    <!-- Right: Page Navigation Buttons -->
    <div class="flex items-center gap-1">
      <!-- Previous Button -->
      <button
        @click="$emit('change-page', currentPage - 1)"
        :disabled="currentPage <= 1"
        class="px-2.5 py-1.5 rounded-lg border border-gray-200 font-medium transition flex items-center gap-1"
        :class="currentPage > 1
          ? 'bg-white text-gray-700 hover:bg-purple-50 hover:text-[#833dff] hover:border-purple-200 cursor-pointer'
          : 'bg-gray-50 text-gray-300 border-gray-100 cursor-not-allowed'"
        title="Previous page"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
        </svg>
        <span>Prev</span>
      </button>

      <!-- Numeric Page Buttons with Ellipses -->
      <template v-for="(p, idx) in visiblePages" :key="idx">
        <span v-if="p === '...'" class="px-2 py-1 text-gray-400">...</span>
        <button
          v-else
          @click="$emit('change-page', p)"
          class="min-w-[30px] h-[30px] rounded-lg text-xs font-semibold transition cursor-pointer flex items-center justify-center"
          :class="p === currentPage
            ? 'bg-[#833dff] text-white shadow-2xs'
            : 'bg-white border border-gray-200 text-gray-700 hover:bg-purple-50 hover:text-[#833dff] hover:border-purple-200'"
        >
          {{ p }}
        </button>
      </template>

      <!-- Next Button -->
      <button
        @click="$emit('change-page', currentPage + 1)"
        :disabled="currentPage >= totalPages"
        class="px-2.5 py-1.5 rounded-lg border border-gray-200 font-medium transition flex items-center gap-1"
        :class="currentPage < totalPages
          ? 'bg-white text-gray-700 hover:bg-purple-50 hover:text-[#833dff] hover:border-purple-200 cursor-pointer'
          : 'bg-gray-50 text-gray-300 border-gray-100 cursor-not-allowed'"
        title="Next page"
      >
        <span>Next</span>
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  currentPage: { type: Number, required: true },
  totalPages: { type: Number, required: true },
  totalReports: { type: Number, required: true },
  pageSize: { type: Number, required: true },
  paginationRange: { type: Object, required: true },
  visiblePages: { type: Array, required: true },
  /** What is counted: reports, by default. */
  noun: { type: String, default: 'reports' },
  sizes: { type: Array, default: () => [10, 25, 50] }
})

defineEmits(['change-page', 'update:pageSize'])
</script>
