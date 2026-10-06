// 工作台 Run Rail 派生 store：本轮工具列表 / 本会话工件，全部从 chat-store 的会话投影派生，不新开后端；tool.start / tool.complete 由 app/runtime/gateway-event-router.ts 写入该投影。

import { atom, computed } from 'nanostores'

import { $chatMessageBodies, $chatMessageList } from '@/modules/conversation'
import { deepEqual } from '@/shared/lib/deep-equal'

export interface ToolStep {
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
}

export const $isRailOpen = atom<boolean>(true)

// computed 每次重算都产出新对象；结构化全等未变则复用旧引用，nanostores 引用比较不通知，避免流式期间整树重渲染。
let lastArtifacts: RailArtifact[] = []
let lastRound: RunRound | null = null

// 本轮：会话尾部最近一条已有正文的 assistant 消息；只看这一条，不同回合的输出不在右栏展示。
export const $runRound = computed([$chatMessageList, $chatMessageBodies], (list, bodies) => {
  const round = list.findLast(item => item.role === 'assistant' && bodies[item.id])
  const active = round && bodies[round.id]

  if (!active) {
    return null
  }

  const running = Boolean(active.toolName) || active.streaming === true
  const tools = active.tools?.length ? active.tools : active.toolName ? [active.toolName] : []

  const steps: ToolStep[] = tools.map((name, idx) => ({
    active: idx === tools.length - 1 && running,
    name
  }))

  const next = { active: running, steps }
  const cached = lastRound

  if (cached !== null && deepEqual(cached, next)) {
    return cached
  }

  lastRound = next

  return lastRound
})

// 本会话工件：所有 assistant 消息携带的媒体，按时间倒序去重；不区分当前轮次——右栏「本会话工件」按会话维度累积。
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
        url: m.url
      })
    }
  }

  const cached = lastArtifacts

  if (deepEqual(cached, artifacts)) {
    return cached
  }

  lastArtifacts = artifacts

  return artifacts
})
