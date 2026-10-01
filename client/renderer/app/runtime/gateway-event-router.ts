import { $chatSessionId } from '@/modules/conversation'
import { onJournalEvent } from '@/modules/memory'
import { onPostEvent } from '@/modules/posts'
import { onSceneEvent } from '@/modules/scene'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $gateway } from '@/shared/store/gateway'

import { $devMode, pushDevLog } from './dev-log'
import type { EventRouteContext } from './gateway-event-util'
import { handleCharacterEvent } from './handlers/character-events'
import { handleConversationEvent } from './handlers/conversation-events'
import { handleDeliveryEvent } from './handlers/delivery-events'
import { handleToolCall, handleToolCancel, handleToolComplete, handleToolStart } from './handlers/tool-dispatch'

// 网关事件路由：只保留分派、窗口角色校验与公共守卫；各能力状态更新在 handlers/，跨模块后续动作进 app/workflows。精灵窗宿主与代理窗口共用；宿主专属 Runner 分发在 handlers/tool-dispatch。

export function handleGatewayEvent(event: GatewayEvent): void {
  // 仅在冷启动 hydrateAuth 尚未完成（'pending'）时丢弃 WSEvent：无用户态，事件无主。'unauthenticated' 不丢弃——登出 race 里到达的 message.complete 还要落地，否则流式 chat 卡 thinking；跨会话污染由下方 session_id 闸门兜底。
  if ($auth.get().kind === 'pending') {
    log.warn('events', 'Discarded event during pending auth:', event.type)

    return
  }

  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
  }

  // 聊天回合事件（message.*/tool.*/error）携带 session_id：来自未查看会话的事件不应作用于可见聊天（如后台任务会话的工具帧），否则用户会看到它们像主会话回复。WSEvent 驱动的事件（companion.message/mood、avatar.regenerated）没有 session_id，直接放行。
  if (event.session_id !== undefined) {
    const current = $chatSessionId.get()

    if (current === null || event.session_id !== current) {
      return
    }
  }

  const ctx: EventRouteContext = { isProxy: $gateway.get()?.isProxy ?? false }

  switch (event.type) {
    case 'message.start':

    case 'message.delta':

    case 'message.reasoning.delta':

    case 'message.break':

    case 'message.persisted':

    case 'message.complete':

    case 'message.voice':

    case 'message.media':

    case 'message.deleted':

    case 'message.edited':

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

    case 'tool.cancel':
      handleToolCancel(event, ctx)

      break

    case 'tool.complete':
      handleToolComplete()

      break

    case 'companion.mood':

    case 'companion.character_card.updated':

    case 'companion.outfit.updated':

    case 'companion.action.catalog_changed':

    case 'companion.action.job_updated':

    case 'companion.action.play_requested':

    case 'companion.video.activated':

    case 'companion.video.failed':

    case 'companion.video.progress':

    case 'companion.video.ready':

    case 'avatar.regenerated':
      handleCharacterEvent(event)

      break

    case 'companion.message':

    case 'system.notification':

    case 'video_gen.completed':

    case 'video_gen.failed':

    case 'channel.status':

    case 'channel.peer_request':
      handleDeliveryEvent(event)

      break

    case 'companion.scene.activated':

    case 'companion.scene.updated':
      onSceneEvent(event)

      break

    case 'companion.post.created':

    case 'companion.post.comment':

    case 'companion.post.comment.deleted':
      onPostEvent(event)

      break

    case 'companion.diary.upserted':
      onJournalEvent(event)

      break

    default:
      break
  }
}
