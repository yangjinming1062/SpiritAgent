// 工作台 Run Rail 派生 store：本轮工具列表 / 本会话工件，全部从现有会话投影。
//
// 不新开后端；事件流订阅 `companion.events`（`tool.start` / `tool.complete`），
// 历史与媒体从 chat-store 派生。

import { atom, computed } from 'nanostores'

import { $chatMessageBodies, $chatMessageList, type ChatMessageBody } from '@/modules/conversation'

export interface ToolStep {
  /** 工具名称（model_info 派生） */
  name: string
  /** 是否为当前正在跑的步骤（最后一条且 assistant 回合尚未 finalize） */
  active: boolean
}

export interface RunRound {
  /** 工具步骤顺序列表 */
  steps: ToolStep[]
  /** 当前回合是否还在 in-flight */
  active: boolean
}

export interface RailArtifact {
  id: string
  kind: 'image' | 'video' | 'audio'
  audioUrl?: string
  url: string
  /** 来自哪条消息；用于追溯 */
  messageId: string
}

export const $isRailOpen = atom<boolean>(true)

export function setRailOpen(open: boolean): void {
  $isRailOpen.set(open)
}

export function toggleRail(): void {
  $isRailOpen.set(!$isRailOpen.get())
}

// 找到当前轮次：会话尾部第一条 assistant 消息即视为「本轮」入口；
// 遇到下一条 user 消息则停止向前扫描（不同回合的输出不在右栏展示）。
export const $runRound = computed([$chatMessageList, $chatMessageBodies], (list, bodies) => {
  let active: ChatMessageBody | undefined
  let activeId: string | undefined

  for (let i = list.length - 1; i >= 0; i--) {
    const item = list[i]

    if (active === undefined && item.role === 'assistant') {
      active = bodies[item.id]
      activeId = item.id
    }

    if (active !== undefined && item.role === 'user') {
      break
    }
  }

  if (!active || !activeId) {
    return null
  }

  const tools = active.tools?.length ? active.tools : active.toolName ? [active.toolName] : []

  const steps: ToolStep[] = tools.map((name, idx) => ({
    active: idx === tools.length - 1 && (Boolean(active.toolName) || active.streaming === true),
    name
  }))

  return {
    active: Boolean(active.toolName) || active.streaming === true,
    steps
  } satisfies RunRound | null
})

// 本会话工件：所有 assistant 消息携带的媒体，按时间倒序去重；
// 不区分当前轮次——右栏「本会话工件」按会话维度累积。
export const $artifacts = computed([$chatMessageList, $chatMessageBodies], (list, bodies) => {
  const artifacts: RailArtifact[] = []
  const seen = new Set<string>()

  for (let i = list.length - 1; i >= 0; i--) {
    const item = list[i]
    const body = bodies[item.id]

    if (!body?.media?.length) {
      continue
    }

    for (const m of body.media) {
      const key = `${m.type}:${m.url}`

      if (seen.has(key)) {
        continue
      }

      seen.add(key)
      artifacts.push({
        id: `${item.id}:${m.url}`,
        kind: m.type,
        audioUrl: m.audio_url,
        messageId: item.id,
        url: m.url
      })
    }
  }

  return artifacts
})
