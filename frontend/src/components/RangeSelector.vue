<script setup lang="ts">
import type { HistoryRange } from '../types'

defineProps<{
  modelValue: HistoryRange
}>()

const emit = defineEmits<{
  'update:modelValue': [value: HistoryRange]
}>()

const ranges: Array<{ value: HistoryRange; label: string }> = [
  { value: '1h', label: '1 hora' },
  { value: '6h', label: '6 horas' },
  { value: '24h', label: '24 horas' },
  { value: '7d', label: '7 dias' },
]
</script>

<template>
  <div class="segmented-control" role="group" aria-label="Período do histórico">
    <button
      v-for="item in ranges"
      :key="item.value"
      type="button"
      :class="{ active: modelValue === item.value }"
      @click="emit('update:modelValue', item.value)"
    >
      {{ item.label }}
    </button>
  </div>
</template>
