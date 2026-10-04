/** 系统动作选择：空间运动与拖拽归并为当前动作键；媒体层负责素材缺失时回退 idle。 */

import type { Locomotion } from '../spatial'

import type { SystemActionKey } from './types'

export interface SystemActionInput {
  /** spatial 的运动状态；drag 优先于行走。 */
  locomotion: Locomotion
  /** 最近一次容器位移的 x 方向符号（-1 左 / +1 右 / 0 未变）。 */
  deltaXSign: number
  /** 空间状态要求的探身姿态；行走和拖拽中的运动动作优先。 */
  peekAction?: 'peek_left' | 'peek_right' | null
}

export function resolveSystemAction(input: SystemActionInput): SystemActionKey {
  if (input.locomotion === 'drag') {
    return 'drag'
  }

  if (input.locomotion === 'walk') {
    if (input.deltaXSign < 0) {
      return 'walk_left'
    }

    if (input.deltaXSign > 0) {
      return 'walk_right'
    }

    // 方向未知（起步/同位）时回退待机，避免误触发按需生成。
    return 'idle'
  }

  if (input.peekAction) {
    return input.peekAction
  }

  return 'idle'
}
