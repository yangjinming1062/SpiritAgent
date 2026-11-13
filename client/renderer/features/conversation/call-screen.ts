import { atom, computed } from 'nanostores'

// 计数而非布尔：同一渲染进程内先后挂载多个通话视图时，后发生的清理不能关掉仍在通话的视图。
const $callScreenCount = atom(0)

export const $callScreenOpen = computed($callScreenCount, count => count > 0)

export function beginCallScreen(): () => void {
  $callScreenCount.set($callScreenCount.get() + 1)
  let released = false

  return () => {
    if (released) {
      return
    }

    released = true
    $callScreenCount.set(Math.max(0, $callScreenCount.get() - 1))
  }
}
