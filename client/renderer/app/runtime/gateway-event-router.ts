import { syncConversationActivity } from '@/app/workflows/conversation-activity'
import { isSpriteOverlayVisible } from '@/app/workflows/proactive-delivery'
import { applyServerVoiceUpdate, onCharacterEvent, triggerFootGlowPulse } from '@/features/character'
import {
  type ConversationRuntime,
  findConversationRuntime,
  handleConversationEvent,
  invalidateSessionHistory,
  onSessionListChanged,
  onTopicFeedbackUpdated,
  requestSessionSnapshot,
  syncSessionHistory
} from '@/features/conversation'
import { onOutfitEvent } from '@/features/creation'
import { onActionEvent } from '@/features/creation/actions'
import { onSceneEvent } from '@/features/creation/environment'
import {
  enqueueInteraction,
  enqueueInteractionForward,
  handleVisualEvent,
  refreshVisualPresentation
} from '@/features/creation/visuals'
import { handleLifeUpdated } from '@/features/life'
import { onJournalEvent } from '@/features/life/diary'
import { onPostEvent } from '@/features/life/posts'
import { invalidateVoiceCatalog } from '@/features/speech'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $gateway } from '@/shared/store/gateway'
import { $surfaceRole } from '@/shared/store/surfaces'

import { $devMode, pushDevLog } from './dev-log'
import { handleDeliveryEvent } from './handlers/delivery-events'
import { handleToolComplete, handleToolStart } from './handlers/tool-dispatch'

/** 会话事件只改所属会话投影；精灵活动与反馈脉冲等跨功能表现由应用层在此应用。 */
function dispatchConversationEvent(event: GatewayEvent, runtime: ConversationRuntime): void {
  const effects = handleConversationEvent(event, runtime, { spriteOverlayVisible: isSpriteOverlayVisible })

  if (effects.syncActivity) {
    syncConversationActivity()
  }

  if (effects.footGlow === 'completed') {
    triggerFootGlowPulse('completed', 1200)
  } else if (effects.footGlow === 'failed') {
    triggerFootGlowPulse('failed', 2000)
  }
}

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
    event.type === 'message.voice' || event.type === 'message.media' || event.type === 'session.call_recorded'
      ? event.payload.session_id
      : event.session_id

  const runtime = typeof sessionId === 'string' ? findConversationRuntime(sessionId) : undefined

  if (runtime && !runtime.acceptsTurnEvent(turnRequestId(event) ?? undefined)) {
    return
  }

  const isPresentationHost = $gateway.get()?.isPresentationHost ?? false

  switch (event.type) {
    case 'companion.action.updated':
      onActionEvent()

      break

    case 'companion.topic_feedback.updated':
      onTopicFeedbackUpdated(event.payload)

      break

    case 'session.list_changed':
      onSessionListChanged(event.payload)

      break
    case 'session.call_recorded': {
      const id = event.payload.session_id
      invalidateSessionHistory(id)

      if (runtime) {
        const previous = runtime.captureHistorySync()
        void syncSessionHistory({ sessionId: id, request: () => requestSessionSnapshot(id) })
          .then(snapshot => {
            runtime.applyRemoteSnapshot(snapshot, previous)
          })
          .catch(error => log.warn('voice-call', 'Could not refresh call history', error))
      }

      break
    }

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
        dispatchConversationEvent(event, runtime)
      }

      break

    case 'message.audio':

    case 'message.voice':
    case 'message.media': {
      if (typeof sessionId === 'string') {
        invalidateSessionHistory(sessionId)
      }

      if (runtime) {
        dispatchConversationEvent(event, runtime)
      }

      break
    }

    case 'compress.completed': {
      const payload = event.payload

      if (sessionId && payload.subtype === 'compress_summary') {
        invalidateSessionHistory(sessionId)
      }

      if (runtime) {
        dispatchConversationEvent(event, runtime)
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

    case 'companion.voice.updated':
      // 声音选择同时影响角色偏好与语音目录，由应用层各调一次。
      applyServerVoiceUpdate(event.payload.voice_id)
      invalidateVoiceCatalog()

      break

    case 'companion.mood':

    case 'companion.visual_identity.updated':

    case 'companion.appearance_preparation.updated':
      onCharacterEvent(event)

      break

    case 'companion.outfit.updated':
      // 衣柜状态变化（重绘草稿/确认转正/穿着翻转/删除）只作刷新触发，列表端点仍是真相源。
      onOutfitEvent()

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
      // 环境就绪或删除会改变当前舞台引用的素材，读取一次最新呈现才能换掉旧背景。
      void refreshVisualPresentation()

      break

    case 'companion.post.created':

    case 'companion.posts.read':

    case 'companion.post.comment':

    case 'companion.post.comment.deleted':
      onPostEvent(event)

      break

    case 'companion.diary.created':

    case 'companion.diary.read':
      onJournalEvent(event)

      break

    case 'companion.visual.updated':

    case 'companion.visual.initial_requested':
      handleVisualEvent(event.type, event.payload)

      break
    case 'companion.reaction': {
      // 模型推断的身体反应只作为演出建议；导演按可用素材与冷却决定是否演出。
      // 多窗口投递保留同一事件身份与发生时间，由当前宿主去重并丢弃过时反应。
      const reaction = event.payload.reaction

      const eventId =
        event.stream_id !== undefined && event.seq !== undefined
          ? `${event.stream_id}:${event.seq}`
          : `${event.payload.occurred_at}:${reaction}`

      const occurredAt = Date.parse(event.payload.occurred_at)

      if (reaction === 'look' || reaction === 'smile' || reaction === 'stretch') {
        if (isPresentationHost || $surfaceRole.get() === 'desktop') {
          enqueueInteraction(reaction, eventId, occurredAt)
        } else {
          enqueueInteractionForward(reaction, eventId, occurredAt)
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
