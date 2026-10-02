import type { Dictionary } from '@/shared/strings'

import type { VideoActionKey } from './types'

/** 系统动作键的界面名称；组件传入 useStrings() 的字典以随语言切换重渲染。 */
export function videoActionNames(appearance: Dictionary['living']['appearance']): Record<VideoActionKey, string> {
  return {
    idle: appearance.videoIdle,
    walk_left: appearance.videoWalkLeft,
    walk_right: appearance.videoWalkRight,
    drag: appearance.videoDrag,
    peek_left: appearance.videoPeekLeft,
    peek_right: appearance.videoPeekRight
  }
}
