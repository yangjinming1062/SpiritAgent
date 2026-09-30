import type { MemoryToolScope, RunnerCallOutcome } from '@ipc/contracts'

import {
  $spriteState,
  findWindowByKeyword,
  performRitualWalk,
  setSpriteState,
  type WindowGeom
} from '@/modules/character'
import { $chatSessionId, $chatTurnInFlight, setAssistantTool } from '@/modules/conversation'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $gateway } from '@/shared/store/gateway'
import { getStrings } from '@/shared/strings'

import { decodePayload, type EventRouteContext } from '../gateway-event-util'

// 宿主专属的设备指令分发：tool.call / tool.cancel 只在精灵窗宿主执行（代理窗的事件泵 proxy-runtime 不转发它们），
// 按 call_id 去重重放帧，交互类工具先走仪式行走再 execute。

// click_at 虚拟目标几何的边长（px）：只为 perch 落位与指向方位提供参照，
// 精灵会站到点击点旁而非覆盖它。
const CLICK_GEOM_SIZE = 160
const CLICK_GEOM_HALF = CLICK_GEOM_SIZE / 2

// 遥控回合可并发多个工具，一个先返回不能把仍在跑的复位掉。
let remoteToolDepth = 0

const NOT_EXECUTED_RESULT = { ok: false, error: 'Not executed: the call did not reach the local runner.' }

const OUTCOME_UNKNOWN_RESULT = {
  ok: false,
  error:
    'The local runner call ended without a result, so the outcome is unknown: the tool may or may not have run. ' +
    'Do not rerun it automatically; check its effects or ask the user first.'
}

// 未执行、明确失败与结果未知分别回传（DESIGN「故障体验」）；结果未知不能报成失败，
// 否则模型可能重做已发生的副作用（PROTOCOL「调用日志与未知结果」）。明确失败带 Runner 的原因，供模型修正调用。
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

// 已受理过的设备指令 call_id。tool.call 进重放缓冲，WS 断开重连时会被重发——没有这道去重，
// 一条「删文件」会在本机执行第二次（后端的 resolve_future 只是丢弃迟到结果，拦不住已发生的副作用）。
const seenToolCalls = new Set<string>()
const SEEN_TOOL_CALL_CAP = 500

// 已受理、尚未收尾的设备调用。后端中断回合时逐个下发 tool.cancel：尚未交给 Runner 的不再执行，
// 已在执行的请 Runner 取消；两种情况都不回传结果（后端已撤销等待）。
const activeToolCalls = new Map<string, { cancelled: boolean; running: boolean }>()

function markToolCallSeen(callId: string): boolean {
  if (seenToolCalls.has(callId)) {
    return false
  }

  // 上限对齐服务端重放缓冲容量；超出后按插入序淘汰最旧的，重放窗口内的 id 不会被提前丢掉。
  if (seenToolCalls.size >= SEEN_TOOL_CALL_CAP) {
    seenToolCalls.delete(seenToolCalls.values().next().value as string)
  }

  seenToolCalls.add(callId)

  return true
}

function releaseRemoteTool(): void {
  remoteToolDepth = Math.max(0, remoteToolDepth - 1)

  // force：IDLE（10）< WORKING（70），没有 force 会被优先级门控静默拒绝，精灵永久卡在工作姿态。
  // 仅在状态仍是工作态（或仪式行走留下的 interacting 瞬态）时复位；可见会话的回合仍在进行时
  // 由它自己的 tool.complete / message.complete 收尾。
  if (remoteToolDepth === 0 && !$chatTurnInFlight.get()) {
    const current = $spriteState.get()

    if (current === 'working' || current === 'interacting') {
      setSpriteState('idle', { force: true })
    }
  }
}

export function handleToolStart(event: GatewayEvent): void {
  // 全局 WORKING 入口——所有工具（后端 / memory / runner）
  // 在执行开始前都会发 tool_start，因此无论工具位置如何精灵都会进入 WORKING。
  // tool.call（下方）只针对 Runner 工具触发，并携带 IPC 分发所需的参数。
  const p = decodePayload<{ name?: string }>(event.payload)

  setAssistantTool(p?.name ?? getStrings().chat.tools.genericName)
  setSpriteState('working')
}

export function handleToolCall(event: GatewayEvent, ctx: EventRouteContext): void {
  // 仅 Runner 分发——桌面回合的 WORKING 由 tool.start 设置。
  // tool.call 是用户级设备指令（不带 session_id、按 call_id 关联），因此不经路由的会话闸门；
  // 缺少 bridge 或 call_id 时后端的等待会在 300 秒后超时并上报错误。
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

  // 该回合的帧是否落在用户正看着的会话里。不是则 tool.start / message.complete 都被上方
  // 会话闸门拦下，没有任何帧能驱动或复位精灵，只能由本分支自持工作态，让「伙伴在帮忙」的
  // 叙事在桌面成立（ARCHITECTURE「工具与表达」）。
  // 用 session_id 比对而非枚举回合类型：遥控、自主、子 agent 乃至以后新增的回合种类都自动落对，
  // 枚举法漏一种就是「机器在动、精灵发呆」，且不会有任何报错。
  const selfDriven = !p.headless && (!p.session_id || p.session_id !== $chatSessionId.get())

  if (selfDriven) {
    remoteToolDepth += 1
    setSpriteState('working', { force: true })
  }

  // fire-and-forget 调用 Runner 并把结果回传，让后端的
  // 等待解析完成；工具错误不得冒泡到本处理器。
  const gateway = $gateway.get()
  const callId = p.call_id
  const call = { cancelled: false, running: false }

  activeToolCalls.set(callId, call)

  void (async () => {
    try {
      const args = p.args ?? {}

      // 仪式行走的解析目标：
      // - system.click_at 的目标就是点击坐标本身（包成虚拟窗口几何，走到旁边
      //   后 execute 即那次点击，不再额外补一次 click → 双击）；
      // - open_application / browser_* 按名称或 URL 匹配既有窗口；
      //   关键词缺失时重试也不会有结果，直接走常规调用。
      let findTarget: (() => Promise<WindowGeom | null>) | null = null
      let previewClick = true

      if (name === 'system.click_at') {
        const cx = Number(args.x)
        const cy = Number(args.y)

        if (Number.isFinite(cx) && Number.isFinite(cy)) {
          const geom: WindowGeom = {
            x: cx - CLICK_GEOM_HALF,
            y: cy - CLICK_GEOM_HALF,
            w: CLICK_GEOM_SIZE,
            h: CLICK_GEOM_SIZE
          }

          findTarget = () => Promise.resolve(geom)
          previewClick = false
        }
      } else {
        const keyword = String(args.name ?? args.url ?? args.keyword ?? '')

        if (keyword.trim()) {
          findTarget = () => findWindowByKeyword(keyword)
        }
      }

      // 仪式行走期间回合已被中断的调用不再交给 Runner。
      const dispatch = (): Promise<null | RunnerCallOutcome> => {
        if (call.cancelled) {
          return Promise.resolve(null)
        }

        call.running = true

        return runnerDispatchCall({ args, callId, name, skillScope: p.skill_scope })
      }

      let outcome: null | RunnerCallOutcome

      try {
        outcome = findTarget ? await performRitualWalk(findTarget, dispatch, { previewClick }) : await dispatch()
      } catch (err) {
        // 尚未交给 Runner 的调用确定没有执行；已交出后的 IPC 失败无法判断请求是否到达 Runner。
        log.warn('events', `runner tool ${name} (${callId}) failed:`, err)
        outcome = call.running ? { status: 'unknown' } : { status: 'not_executed' }
      }

      if (!outcome || call.cancelled) {
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

      if (selfDriven) {
        releaseRemoteTool()
      }
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

  if (call.running) {
    void window.spiritagent?.runnerCancel?.(callId).catch(err => {
      log.warn('events', `runner cancel for ${callId} failed:`, err)
    })
  }
}

export function handleToolComplete(): void {
  // 全局 WORKING 出口——所有工具的 finally 块都会发 tool_end。
  // force：THINKING（50）< WORKING（70），没有 force 的话优先级门控会静默拒绝该转换。
  setAssistantTool(null)
  setSpriteState('thinking', { force: true })
}
