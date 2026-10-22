import { atom, computed } from 'nanostores'

import { $surfaceOpen } from './surfaces'

export const $whisperOpen = atom<boolean>(false)
export const $desktopChatActive = atom(false)
export const $mainChatActive = atom(false)

// "用户现在在对话界面里"：桌面聊天激活，或主聊天在窗口展开时激活，或轻语在窗口收起时展开。
export const $chatVisible = computed(
  [$surfaceOpen, $whisperOpen, $desktopChatActive, $mainChatActive],
  (surface, whisper, desktop, main) => desktop || (main && surface) || (whisper && !surface)
)
