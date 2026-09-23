import { atom } from 'nanostores'
import { useCallback, useEffect, useRef } from 'react'

import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'

/** 命中探测（舞台像素坐标）：返回 true 命中身体 / false 透明 / null 无数据。 */
export const $videoHitTest = atom<((nx: number, ny: number) => boolean | null) | null>(null)

/** 视频 alpha 遮罩命中：缺席（桌面蛋 / 加载空挡）回退整矩形，避免空白区挡住下层点击。
 *  异步遮罩落地后主动 probe，保证指针静止时也按最新遮罩重判（README §6）。 */
export function useVideoPixelHitTest(windowId = 0): (x: number, y: number) => boolean {
  const hitVideoRef = useRef<((x: number, y: number) => boolean | null) | null>(null)

  useEffect(
    () =>
      $videoHitTest.subscribe(fn => {
        hitVideoRef.current = fn
        probeInteractiveRegions(windowId)
      }),
    [windowId]
  )

  return useCallback((x: number, y: number): boolean => {
    const probeVideo = hitVideoRef.current

    if (probeVideo) {
      const result = probeVideo(x, y)

      if (result !== null) {
        return result
      }
    }

    return true
  }, [])
}
