<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

defineProps({ isProcessing: { type: Boolean, default: false } })
</script>

<template>
  <footer class="status-bar">
    <div class="status-left">
      <span class="status-dot" :class="{ active: isProcessing }"></span>
      <span>{{ isProcessing ? '处理中' : '就绪' }}</span>
    </div>
    <div class="status-right">
      <span class="status-item version-tag">v{{ __APP_VERSION__ }}</span>
      <span class="status-divider">·</span>
      <span class="status-item">Sage</span>
    </div>
  </footer>
</template>

<style scoped>
.status-bar {
  height: 22px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 10px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-light);
  font-size: 10px; color: var(--text-faint);
}

.status-left, .status-right {
  display: flex; align-items: center; gap: 5px;
}

.status-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--success); flex-shrink: 0;
}
.status-dot.active { background: var(--accent); animation: pulse 0.8s ease-in-out infinite; }

.status-divider { color: var(--text-faint); opacity: 0.5; }
.version-tag {
  font-variant-numeric: tabular-nums;
  font-weight: 550;
  color: var(--text-secondary);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
