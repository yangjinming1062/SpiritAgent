import { useStore } from '@nanostores/react'

import { $chatSessionKind } from './chat-store'
import { $currentSessionKind } from './session-list-store'

// 多个入口（生活空间 / 工作台 / 轻语）共用：当前会话对用户是否只读。
// IM 渠道会话在桌面端只读展示，不允许新建/编辑消息。
export function useIsReadOnlySession(): boolean {
  const chatSessionKind = useStore($chatSessionKind)
  const currentSessionKind = useStore($currentSessionKind)
  const sessionKind = chatSessionKind || currentSessionKind || ''

  return sessionKind === 'im'
}
