<template>
  <div class="search-input-wrapper" ref="wrapperRef">
    <div class="input-inner">
      <input
        type="text"
        :value="modelValue"
        @input="handleInput"
        @focus="handleFocus"
        @keydown.down.prevent="navigate(1)"
        @keydown.up.prevent="navigate(-1)"
        @keydown.enter.prevent="selectActive"
        :placeholder="placeholder"
        class="terminal-search-input"
      />
      <span v-if="loading" class="search-spinner"></span>
    </div>

    <!-- 下拉候选列表 -->
    <div v-if="showDropdown && candidates.length > 0" class="dropdown-menu">
      <div
        v-for="(item, idx) in candidates"
        :key="item.code"
        class="dropdown-item"
        :class="{ active: activeIndex === idx }"
        @mousedown.prevent="selectItem(item)"
        @mouseenter="activeIndex = idx"
      >
        <div class="item-left">
          <span class="stock-name">{{ item.name }}</span>
          <span class="stock-code">{{ item.code }}</span>
        </div>
        <div class="item-right">
          <span v-if="item.price > 0" class="stock-price">¥{{ Number(item.price).toFixed(2) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api'

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '输入拼音首字母(如PAYH)、代码、名称...'
  }
})

const emit = defineEmits(['update:modelValue', 'select'])

const wrapperRef = ref(null)
const candidates = ref([])
const loading = ref(false)
const showDropdown = ref(false)
const activeIndex = ref(0)
let debounceTimer = null

function handleInput(e) {
  const val = e.target.value
  emit('update:modelValue', val)

  if (debounceTimer) clearTimeout(debounceTimer)
  if (!val.trim()) {
    candidates.value = []
    showDropdown.value = false
    return
  }

  debounceTimer = setTimeout(async () => {
    loading.value = true
    try {
      const res = await api.searchStocks(val.trim())
      candidates.value = res || []
      showDropdown.value = candidates.value.length > 0
      activeIndex.value = 0
    } catch (err) {
      candidates.value = []
    } finally {
      loading.value = false
    }
  }, 250)
}

function handleFocus() {
  if (candidates.value.length > 0) {
    showDropdown.value = true
  }
}

function navigate(direction) {
  if (!showDropdown.value || candidates.value.length === 0) return
  activeIndex.value = (activeIndex.value + direction + candidates.value.length) % candidates.value.length
}

function selectActive() {
  if (showDropdown.value && candidates.value[activeIndex.value]) {
    selectItem(candidates.value[activeIndex.value])
  }
}

function selectItem(item) {
  emit('update:modelValue', `${item.name} (${item.code})`)
  emit('select', item)
  showDropdown.value = false
}

function handleClickOutside(e) {
  if (wrapperRef.value && !wrapperRef.value.contains(e.target)) {
    showDropdown.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<style scoped>
.search-input-wrapper {
  position: relative;
  width: 100%;
}

.input-inner {
  position: relative;
  display: flex;
  align-items: center;
}

.terminal-search-input {
  width: 100%;
  background: #161b22;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 7px 12px;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.2s;
}

.terminal-search-input:focus {
  border-color: #58a6ff;
  box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2);
}

.search-spinner {
  position: absolute;
  right: 10px;
  width: 14px;
  height: 14px;
  border: 2px solid #30363d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.dropdown-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  max-height: 220px;
  overflow-y: auto;
  z-index: 100;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
}

.dropdown-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.1s;
  border-bottom: 1px solid #21262d;
}

.dropdown-item:last-child {
  border-bottom: none;
}

.dropdown-item.active,
.dropdown-item:hover {
  background: #21262d;
}

.item-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stock-name {
  color: #f0f6fc;
  font-size: 13px;
  font-weight: 600;
}

.stock-code {
  color: #58a6ff;
  font-family: monospace;
  font-size: 12px;
}

.stock-price {
  color: #8b949e;
  font-size: 12px;
  font-family: monospace;
}
</style>
