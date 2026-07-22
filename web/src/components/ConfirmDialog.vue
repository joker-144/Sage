<script setup>
defineProps({
  visible: Boolean,
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '确定要执行此操作吗？' },
})
const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <transition name="fade">
    <div v-if="visible" class="confirm-overlay" @click.self="emit('cancel')">
      <div class="confirm-dialog">
        <div class="confirm-title">{{ title }}</div>
        <p class="confirm-message">{{ message }}</p>
        <div class="confirm-actions">
          <button class="btn-cancel" @click="emit('cancel')">取消</button>
          <button class="btn-confirm" @click="emit('confirm')">确认删除</button>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.confirm-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
}
.confirm-dialog {
  background: var(--bg-elevated, #ffffff);
  border: 1px solid var(--border, rgba(15, 23, 42, 0.08));
  border-radius: var(--radius-lg);
  padding: 22px 24px;
  width: 360px;
  box-shadow: var(--shadow-lg);
}
.confirm-title {
  font-size: 15px; font-weight: 650;
  color: var(--text-primary, #0f172a);
  margin-bottom: 8px;
}
.confirm-message {
  font-size: 12.5px; color: var(--text-muted, #64748b);
  line-height: 1.5; margin: 0 0 18px;
}
.confirm-actions {
  display: flex; justify-content: flex-end; gap: 8px;
}
.btn-cancel, .btn-confirm {
  padding: 7px 16px; border-radius: 6px;
  font-family: var(--font-sans); font-size: 12px; font-weight: 500;
  cursor: pointer; border: 1px solid var(--border, #333);
  transition: all 0.15s var(--ease-out-expo);
}
.btn-cancel {
  background: var(--bg-surface, #ffffff);
  color: var(--text-secondary, #475569);
}
.btn-cancel:hover { background: var(--bg-hover, #f1f5f9); }
.btn-confirm {
  background: var(--error);
  color: white; border-color: var(--error);
}
.btn-confirm:hover { opacity: 0.85; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.15s var(--ease-out-expo); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
