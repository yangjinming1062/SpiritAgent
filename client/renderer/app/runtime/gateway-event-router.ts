import { $chatSessionId, findConversationRuntime, getConversationRuntime } from '@/modules/conversation'
import { onJournalEvent } from '@/modules/memory'
import { onPostEvent } from '@/modules/posts'
import { onSceneEvent } from '@/modules/scene'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
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
  // 换号及登出期间的旧连接事件没有当前账户所有权；断连收尾由runtime生命周期负责。
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
  }

  // 一份会话runtime接收一次事件；主对话与固定轻语只订阅共享投影。
  const runtime =
    event.session_id !== undefined
      ? findConversationRuntime(event.session_id)
      : getConversationRuntime($chatSessionId.get())

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
      if (runtime) {
        handleConversationEvent(event, ctx, runtime)
      }

      break

    case 'tool.start':
      if (runtime) {
        handleToolStart(event, runtime)
      }

      break

    case 'tool.call':
      handleToolCall(event, ctx)

      break

    case 'tool.cancel':
      handleToolCancel(event, ctx)

      break

    case 'tool.complete':
      if (runtime) {
        handleToolComplete(runtime)
      }

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

    case 'companion.posts.read':

    case 'companion.post.comment':

    case 'companion.post.comment.deleted':
      onPostEvent(event)

      break

    case 'companion.diary.created':

    case 'companion.diary.read':

    case 'companion.diary.deleted':
      onJournalEvent(event)

      break

    default:
      break
  }
}
