import { ref, watch, onUnmounted, isRef } from 'vue'

/**
 * 数字滚动 composable — 目标值变化时从当前显示值平滑动画到目标值
 *
 * 用法：
 *   const display = useCountUp(() => stats.totalConversations)
 *   // 模板：{{ display }}
 *
 * @param {Ref<number>|(() => number)} source - 目标值的 ref 或 getter
 * @param {number} duration - 动画时长（毫秒）
 * @returns {Ref<number>} display - 动画过程中的实时显示值（整数）
 */
export function useCountUp(source, duration = 900) {
  const get = isRef(source) ? () => source.value : source
  const display = ref(0)
  let raf = null

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3)
  }

  function animate(to) {
    cancelAnimationFrame(raf)
    const from = display.value
    if (from === to) return
    const startTime = performance.now()

    function step(now) {
      const t = Math.min(1, (now - startTime) / duration)
      display.value = Math.round(from + (to - from) * easeOutCubic(t))
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
  }

  // 目标值变化时触发动画（跳过 0：加载中不播）
  watch(get, (v) => {
    if (v && v > 0) animate(v)
  })

  onUnmounted(() => cancelAnimationFrame(raf))

  return display
}
