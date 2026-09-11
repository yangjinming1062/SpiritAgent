import { useEffect } from 'react'

import { useLatestRef } from './use-latest-ref'

export interface UseEscapeKeyOptions {
  // false 时不挂监听；用于"对话框已关闭时跳过挂载"等场景。
  enabled?: boolean
  // true 时挂载但忽略命中；用于"提交进行中仍允许外层 Esc，但自身不响应"。
  busy?: boolean
  // true = capture 阶段（modal 默认）；false = bubble 阶段（灯箱等需穿透外层）。
  capture?: boolean
  preventDefault?: boolean
  stopPropagation?: boolean
}

// 统一的"挂 window 键盘事件、命中 Esc、调用 handler"原语，
// 取代 ConfirmDialog / WizardModal / PortraitLightbox / ActivationOverlay /
// seed3d-wizard 中重复出现的 `addEventListener('keydown', …, true)` 样板。
// handler 走 useLatestRef 镜像最新引用，调用方无需 useCallback。
export function useEscapeKey(
  handler: () => void,
  {
    enabled = true,
    busy = false,
    capture = true,
    preventDefault = true,
    stopPropagation = true
  }: UseEscapeKeyOptions = {}
): void {
  const handlerRef = useLatestRef(handler)

  useEffect(() => {
    if (!enabled) {
      return
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape' || busy) {
        return
      }

      if (preventDefault) {
        e.preventDefault()
      }

      if (stopPropagation) {
        e.stopPropagation()
      }

      handlerRef.current()
    }

    window.addEventListener('keydown', onKey, capture)

    return () => window.removeEventListener('keydown', onKey, capture)
  }, [enabled, busy, capture, preventDefault, stopPropagation, handlerRef])
}
