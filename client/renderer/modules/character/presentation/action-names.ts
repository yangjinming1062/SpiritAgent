import type { Dictionary } from '@/shared/strings'

import type { SystemActionKey } from './types'

/** 系统动作键的界面名称；组件传入 useStrings() 的字典以随语言切换重渲染。 */
export function systemActionNames(appearance: Dictionary['living']['appearance']): Record<SystemActionKey, string> {
  return {
    idle: appearance.videoIdle,
    walk_left: appearance.videoWalkLeft,
    walk_right: appearance.videoWalkRight,
    drag: appearance.videoDrag,
    peek_left: appearance.videoPeekLeft,
    peek_right: appearance.videoPeekRight
  }
}
