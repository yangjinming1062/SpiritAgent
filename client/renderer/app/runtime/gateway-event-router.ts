import { $chatSessionId } from '@/modules/conversation'
import { onJournalEvent } from '@/modules/memory'
import { onBackdropEvent } from '@/modules/room'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $chatVisible } from '@/shared/store/chat-visibility'
import { $gateway } from '@/shared/store/gateway'

import { $devMode, pushDevLog } from './dev-log'
import type { EventRouteContext } from './gateway-event-util'
import { handleCharacterEvent } from './handlers/character-events'
import { handleConversationEvent } from './handlers/conversation-events'
import { handleDeliveryEvent } from './handlers/delivery-events'
import { handleToolCall, handleToolComplete, handleToolStart } from './handlers/tool-dispatch'

// 网关事件路由：只保留分派、窗口角色校验与公共守卫；各能力的状态更新在
// handlers/ 按能力组织，跨模块的后续动作进 app/workflows。
// 精灵窗宿主与代理窗口共用本路由；宿主专属的 Runner 分发在 handlers/tool-dispatch。

export function handleGatewayEvent(event: GatewayEvent): void {
  // 仅在冷启动 hydrateAuth 尚未完成时（'pending'）丢弃 WSEvent：无用户态，事件无主。
  // 'unauthenticated' 不丢弃：登出 race 里到达的 message.complete / model.ready /
  // companion.2d.ready 还要落地——否则流式 chat 卡 thinking、模型 ready 漏掉让用户
  // 看到旧 model。跨会话污染由事件本身的 session_id 闸门（下方 session_id 过滤段）兜底。
  // 写持久化原子的副作用分支（model.ready / companion.2d.ready）在自己内部用 $auth.kind
  // 二次防御，避免 OPFS / localStorage 串味。
  if ($auth.get().kind === 'pending') {
    log.warn('events', 'Discarded event during pending auth:', event.type)

    return
  }

  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
  }

  // 聊天回合事件（message.start/delta/complete/persisted、tool.*、error）携带发出该事件的会话 session_id。
  // 来自渲染层当前未查看会话的事件不应作用于可见聊天——
  // 例如后台任务会话的工具帧；没有这道门的话，用户会看到它们像主会话回复。
  // WSEvent 驱动的事件（companion.message/affect/mood、model.*、
  // avatar.regenerated）没有 session_id，直接放行。
  if (event.session_id !== undefined) {
    const current = $chatSessionId.get()

    if (current === null || event.session_id !== current) {
      return
    }
  }

  const gw = $gateway.get()
  const isProxy = Boolean(gw && 'isProxy' in gw && gw.isProxy)

  const ctx: EventRouteContext = {
    isProxy,
    // 宿主窗在聊天面板可见时静音；代理窗口（生活空间 / 工作台）始终允许。
    shouldPlayAudio: isProxy || !$chatVisible.get()
  }

  switch (event.type) {
    case 'message.start':

    case 'message.delta':

    case 'message.reasoning.delta':

    case 'message.break':

    case 'message.persisted':

    case 'message.complete':

    case 'message.deleted':

    case 'command.result':

    case 'compress.completed':

    case 'error':
      handleConversationEvent(event, ctx)

      break

    case 'tool.start':
      handleToolStart(event)

      break

    case 'tool.call':
      handleToolCall(event, ctx)

      break

    case 'tool.complete':
      handleToolComplete()

      break

    case 'companion.affect':

    case 'companion.mood':

    case 'model.ready':

    case 'model.gen.progress':

    case 'model.failed':

    case 'companion.2d.ready':

    case 'companion.2d.failed':

    case 'companion.outfit.updated':

    case 'companion.outfit.failed':

    case 'companion.render_mode.changed':

    case 'avatar.regenerated':
      handleCharacterEvent(event, ctx)

      break

    case 'companion.message':

    case 'system.notification':

    case 'video_gen.completed':

    case 'video_gen.failed':

    case 'channel.status':

    case 'channel.peer_request':
      handleDeliveryEvent(event, ctx)

      break

    case 'companion.room.ready':

    case 'companion.room.failed':

    case 'companion.room.invalidated':

    case 'companion.room.progress':
      onBackdropEvent(event)

      break

    case 'companion.moment.created':

    case 'companion.diary.upserted':
      onJournalEvent(event)

      break

    default:
      break
  }
}
