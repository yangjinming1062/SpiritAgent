import { holdRemoteToolActivity, syncConversationActivity } from '@/app/workflows/conversation-activity'
import { type ConversationRuntime, findConversationRuntime } from '@/modules/conversation'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { trimOldest } from '@/shared/lib/trim-oldest'
import { $gateway } from '@/shared/store/gateway'
import { getStrings } from '@/shared/strings'
import type { MemoryToolScope, RunnerCallOutcome } from '@ipc/contracts'

import { decodePayload, type EventRouteContext } from '../gateway-event-util'

const NOT_EXECUTED_RESULT = { ok: false, error: 'Not executed: the call did not reach the local runner.' }

const OUTCOME_UNKNOWN_RESULT = {
  ok: false,
  error:
    'The local runner call ended without a result, so the outcome is unknown: the tool may or may not have run. ' +
    'Do not rerun it automatically; check its effects or ask the user first.'
}

// 未执行、明确失败与结果未知分别回传（DESIGN「故障体验」）；结果未知不能报成失败，否则模型可能重做已发生的副作用。
function runnerCallResult(outcome: RunnerCallOutcome): unknown {
  switch (outcome.status) {
    case 'completed':
      return outcome.result

    case 'failed':
      return { ok: false, error: outcome.error }

    case 'not_executed':
      return NOT_EXECUTED_RESULT

    case 'unknown':
      return OUTCOME_UNKNOWN_RESULT
  }
}

// 已受理过的 call_id：tool.call 进重放缓冲，WS 重连会重发——没有这道去重，一条「删文件」会在本机执行第二次。
const seenToolCalls = new Set<string>()
const SEEN_TOOL_CALL_CAP = 500

// 已交给 Runner 的在途调用：后端中断时请求 Runner 取消，并停止回传结果。
const activeToolCalls = new Map<string, { cancelled: boolean }>()

function markToolCallSeen(callId: string): boolean {
  if (seenToolCalls.has(callId)) {
    return false
  }

  // 上限对齐服务端重放缓冲容量；超出后按插入序淘汰最旧的，重放窗口内的 id 不会被提前丢掉。
  seenToolCalls.add(callId)
  trimOldest(seenToolCalls, SEEN_TOOL_CALL_CAP)

  return true
}

export function handleToolStart(event: GatewayEvent, runtime: ConversationRuntime): void {
  const p = decodePayload<{ name?: string }>(event.payload)

  runtime.setAssistantTool(p.name ?? getStrings().chat.tools.genericName)
  syncConversationActivity()
}

export function handleToolCall(event: GatewayEvent, ctx: EventRouteContext): void {
  // 仅 Runner 分发；tool.call 是用户级设备指令（不带 session_id、按 call_id 关联），不经会话闸门；缺少 bridge 或 call_id 时后端等待到超时。
  const p = decodePayload<{
    name?: string
    args?: Record<string, unknown>
    call_id?: string
    session_id?: string
    headless?: boolean
    skill_scope?: MemoryToolScope
  }>(event.payload)

  const runnerDispatchCall = window.spiritagent?.runnerDispatchCall

  if (ctx.isProxy || !p.call_id || !runnerDispatchCall) {
    return
  }

  // 重放的重复指令直接丢弃：本机副作用不可撤销，宁可让后端等到超时也不能执行第二次。
  if (!markToolCallSeen(p.call_id)) {
    log.warn('events', `duplicate tool.call ${p.call_id} ignored (replayed frame)`)

    return
  }

  const name = p.name ?? ''

  const ownsTurn = p.session_id && findConversationRuntime(p.session_id)?.$chatTurnInFlight.get()
  const releaseActivity = !p.headless && !ownsTurn ? holdRemoteToolActivity() : undefined

  // fire-and-forget 调用 Runner 并回传结果，让后端等待解析完成；工具错误不得冒泡到本处理器。
  const gateway = $gateway.get()
  const callId = p.call_id
  const call = { cancelled: false }

  activeToolCalls.set(callId, call)

  void (async () => {
    try {
      let outcome: RunnerCallOutcome

      try {
        outcome = await runnerDispatchCall({ args: p.args ?? {}, callId, name, skillScope: p.skill_scope })
      } catch (err) {
        // IPC 失败无法判断请求是否到达 Runner。
        log.warn('events', `runner tool ${name} (${callId}) failed:`, err)
        outcome = { status: 'unknown' }
      }

      if (call.cancelled) {
        return
      }

      if (outcome.status !== 'completed') {
        log.warn('events', `runner tool ${name} (${callId}) ended ${outcome.status}`)
      }

      try {
        if (!gateway) {
          throw new Error('gateway unavailable')
        }

        await gateway.request('tool.result', { call_id: callId, result: runnerCallResult(outcome) })
      } catch (err) {
        // 未送达时后端等待按超时收尾，同样视为结果未知。
        log.warn('events', `tool.result for ${name} (${callId}) not delivered:`, err)
      }
    } finally {
      activeToolCalls.delete(callId)

      releaseActivity?.()
    }
  })()
}

export function handleToolCancel(event: GatewayEvent, ctx: EventRouteContext): void {
  const p = decodePayload<{ call_id?: string }>(event.payload)
  const callId = p.call_id
  const call = callId ? activeToolCalls.get(callId) : undefined

  if (ctx.isProxy || !callId || !call || call.cancelled) {
    return
  }

  call.cancelled = true

  void window.spiritagent?.runnerCancel?.(callId).catch(err => {
    log.warn('events', `runner cancel for ${callId} failed:`, err)
  })
}

export function handleToolComplete(runtime: ConversationRuntime): void {
  runtime.setAssistantTool(null)
  syncConversationActivity()
}
