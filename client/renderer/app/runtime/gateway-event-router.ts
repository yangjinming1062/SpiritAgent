import {
  findConversationRuntime,
  invalidateSessionHistory,
  onSessionListChanged,
  onTopicFeedbackUpdated
} from '@/modules/conversation'
import { onJournalEvent } from '@/modules/diary'
import { handleLifeUpdated } from '@/modules/life'
import { onPostEvent } from '@/modules/posts'
import { onSceneEvent } from '@/modules/scene'
import {
  enqueueInteraction,
  enqueueInteractionForward,
  handleVisualEvent,
  refreshActionGroups
} from '@/modules/visuals'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { $auth } from '@/shared/store/auth'
import { $gateway } from '@/shared/store/gateway'
import { $surfaceRole } from '@/shared/store/surfaces'

import { $devMode, pushDevLog } from './dev-log'
import { handleCharacterEvent } from './handlers/character-events'
import { type EventRouteContext, handleConversationEvent } from './handlers/conversation-events'
import { handleDeliveryEvent } from './handlers/delivery-events'
import { handleToolComplete, handleToolStart } from './handlers/tool-dispatch'

function turnRequestId(event: GatewayEvent): string | null {
  switch (event.type) {
    case 'message.start':

    case 'message.delta':

    case 'message.reasoning.delta':

    case 'message.break':

    case 'message.bubble':

    case 'message.complete':

    case 'error':

    case 'tool.start':

    case 'tool.complete':
      return event.payload.request_id

    default:
      return null
  }
}

// 网关事件路由只负责视图分派与窗口守卫；设备调用由主进程消费。

export function handleGatewayEvent(event: GatewayEvent): void {
  // 换号及登出期间的旧连接事件没有当前账户所有权；断连收尾由runtime生命周期负责。
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
  }

  // 语音与媒体 outbox 事件在载荷中标识会话，其余会话事件使用信封路由。
  const sessionId =
    event.type === 'message.voice' || event.type === 'message.media' ? event.payload.session_id : event.session_id

  const runtime = typeof sessionId === 'string' ? findConversationRuntime(sessionId) : undefined

  if (runtime && !runtime.acceptsTurnEvent(turnRequestId(event) ?? undefined)) {
    return
  }

  const ctx: EventRouteContext = { isPresentationHost: $gateway.get()?.isPresentationHost ?? false }

  switch (event.type) {
    case 'companion.action.updated':
      void refreshActionGroups()

      break

    case 'companion.topic_feedback.updated':
      onTopicFeedbackUpdated(event.payload)

      break

    case 'session.list_changed':
      onSessionListChanged(event.payload)

      break

    case 'session.snapshot':

    case 'session.state':

    case 'message.start':

    case 'message.delta':

    case 'message.reasoning.delta':

    case 'message.break':

    case 'message.bubble':

    case 'message.persisted':

    case 'message.complete':

    case 'message.deleted':

    case 'message.edited':

    case 'message.replaced':

    case 'command.result':

    case 'error':
      if (runtime) {
        handleConversationEvent(event, ctx, runtime)
      }

      break

    case 'message.audio':

    case 'message.voice':
    case 'message.media': {
      if (typeof sessionId === 'string') {
        invalidateSessionHistory(sessionId)
      }

      if (runtime) {
        handleConversationEvent(event, ctx, runtime)
      }

      break
    }

    case 'compress.completed': {
      const payload = event.payload

      if (sessionId && payload.subtype === 'compress_summary') {
        invalidateSessionHistory(sessionId)
      }

      if (runtime) {
        handleConversationEvent(event, ctx, runtime)
      }

      break
    }

    case 'tool.start':
      if (runtime) {
        handleToolStart(event, runtime)
      }

      break

    case 'tool.complete':
      if (runtime) {
        handleToolComplete(runtime)
      }

      break

    case 'companion.mood':

    case 'companion.voice.updated':

    case 'companion.character_card.updated':

    case 'companion.outfit.updated':
      handleCharacterEvent(event)

      break

    case 'companion.life.updated':
      handleLifeUpdated()

      break

    case 'companion.message':

    case 'system.notification':

    case 'video_gen.completed':

    case 'video_gen.queue':

    case 'video_gen.failed':
      handleDeliveryEvent(event)

      break

    case 'companion.scene.updated':
      onSceneEvent()

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

    case 'companion.visual.updated':

    case 'companion.visual.initial_requested':
      handleVisualEvent(event.type, event.payload)

      break
    case 'companion.reaction': {
      // 模型推断的身体反应只作为演出建议；导演按可用素材与冷却决定是否演出。
      // 事件到达所有桌面端窗口：宿主窗口直接入队，非宿主窗口经 storage 信号转发给宿主，
      // 两路互斥避免同一事件被消费两次。
      const reaction = event.payload.reaction

      if (reaction === 'look' || reaction === 'smile' || reaction === 'stretch') {
        if (ctx.isPresentationHost || $surfaceRole.get() === 'desktop') {
          enqueueInteraction(reaction)
        } else {
          enqueueInteractionForward(reaction)
        }
      }

      break
    }

    case 'companion.visual.play':
      handleVisualEvent(event.type, event.payload)

      break

    case 'companion.visual.playback':
      handleVisualEvent(event.type, event.payload)

      break

    default:
      break
  }
}
