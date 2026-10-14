import { atom, computed } from 'nanostores'

import { $surfaceOpen } from './surfaces'

// 用户当前正在看的对话承载位置；唯一真相源。
type ChatDestination = 'main' | 'whisper' | 'desktop'

// 轻语只在主窗口收起时呈现。
export const $whisperOpen = atom<boolean>(false)
export const $desktopChatActive = atom(false)
export const $mainChatActive = atom(false)

const $chatDestination = computed(
  [$surfaceOpen, $whisperOpen, $desktopChatActive, $mainChatActive],
  (surface, whisper, desktop, main): ChatDestination | null =>
    desktop ? 'desktop' : main && surface ? 'main' : whisper && !surface ? 'whisper' : null
)

// "用户现在在对话界面里"的布尔简写。消费方只关心看不看聊天时用这个。
export const $chatVisible = computed($chatDestination, dest => dest !== null)
