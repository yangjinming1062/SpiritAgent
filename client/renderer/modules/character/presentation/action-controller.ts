/** 视频动作选择（presentation 语义，视频渲染器执行）：
 * 业务状态、空间运动与拖拽归并为当前动作键；缺素材的动作回退 idle。 */

import type { VideoActionKey } from './types'

export interface VideoActionInput {
  /** spatial 的运动状态；drag 优先于行走，jump 无专属片段回退 idle。 */
  locomotion: 'still' | 'walk' | 'walk_fast' | 'fly' | 'drag' | 'jump'
  /** 最近一次容器位移的 x 方向符号（-1 左 / +1 右 / 0 未变）。 */
  deltaXSign: number
}

export function resolveVideoAction(input: VideoActionInput): VideoActionKey {
  if (input.locomotion === 'drag') {
    return 'drag'
  }

  if (input.locomotion === 'walk' || input.locomotion === 'walk_fast') {
    return input.deltaXSign < 0 ? 'walk_left' : 'walk_right'
  }

  return 'idle'
}

/** 渲染器实际可兑现的动作集合：缺素材的动作回退 idle，不悬空等待。 */
export function pickAvailableClip<K extends string>(requested: K, available: ReadonlySet<string>, fallback: K): K {
  return available.has(requested) ? requested : fallback
}
