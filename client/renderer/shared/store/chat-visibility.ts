import { atom, computed } from 'nanostores'

import { $surfaceOpen } from './surfaces'

// 用户当前正在看的对话承载位置；唯一真相源。
// 取代散落的 `$surfaceOpen === null` + `$whisperOpen` 复合判断。
export type ChatDestination = 'living' | 'workbench' | 'whisper'

// 轻语卡片可见性：独立于 $surfaceOpen（生活空间/工作台是 IPC 驱动的 BrowserWindow，
// 轻语是精灵窗内的浮层）。精灵窗与生活空间/工作台互斥；轻语打开时把这条设为 true。
export const $whisperOpen = atom<boolean>(false)

// "用户现在在对话界面里" 的统一判定：生活空间 / 工作台 / 轻语 三者任一即可。
// 同时是单一字符串（表示具体位置）或 null（都没开）。
export const $chatDestination = computed(
  [$surfaceOpen, $whisperOpen],
  (surface, whisper): ChatDestination | null => surface ?? (whisper ? 'whisper' : null)
)

// "用户现在在对话界面里" 的布尔简写。消费方只关心看不看聊天时用这个。
export const $chatVisible = computed($chatDestination, dest => dest !== null)
