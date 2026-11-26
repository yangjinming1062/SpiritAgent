import { $autoplayVoice, autoplayVoicePref } from '@/features/character'
import {
  $chatSessionId,
  $conversationViews,
  activeVoiceMessageId,
  bindVoicePlaybackUpdates,
  captureVoiceProgress,
  type ChatMessageBody,
  findConversationRuntime,
  getVoicePlaybackStore,
  isConversationActive,
  runtimeForMessage,
  setConversationVoiceSink,
  setVoiceBarControl,
  setVoiceBarFailed,
  setVoiceBarLoading,
  setVoiceBarPaused,
  setVoiceBarPlaying,
  voicePlaybackReady
} from '@/features/conversation'
import {
  bindVoiceBarListeners,
  bindVoiceBarProjection,
  cancelVoiceBar,
  enqueueVoiceBars,
  refreshVoiceAutoplay,
  refreshVoiceBarVisibility,
  restartVoiceBar,
  setVoiceRecording,
  setVoiceSurfaceMounted,
  toggleVoiceBar
} from '@/features/speech'
import { requestResource } from '@/shared/lib/resource-request'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { voicePlaybackKey } from '@ipc/contracts'

let dispose: (() => void) | undefined

function savedVoiceBubble(messageId: string | undefined, body: ChatMessageBody | undefined) {
  return messageId && body?.replyType === 'voice' && body.replyIndex !== undefined
    ? { messageId, bubbleIndex: body.replyIndex, body }
    : null
}

function voiceReference(id: string) {
  const runtime = runtimeForMessage(id)
  const item = runtime?.$chatMessageList.get().find(message => message.id === id)
  const bubble = savedVoiceBubble(item?.backendMessageId, runtime?.$chatMessageBodies.get()[id])
  const sessionId = runtime?.$chatSessionId.get()

  return runtime && sessionId && bubble
    ? {
        ...bubble,
        runtime,
        sessionId,
        key: voicePlaybackKey(bubble.messageId, bubble.bubbleIndex)
      }
    : null
}

export function bindConversationSpeech(): void {
  dispose?.()
  bindVoiceBarProjection({
    isActive: sessionId => $conversationViews.get().length === 0 || isConversationActive(sessionId),
    getVisibleSessionIds: () => {
      const views = $conversationViews.get()

      return views.length
        ? views.flatMap(({ visible, runtime }) => {
            const sessionId = runtime.$chatSessionId.get()

            return visible && sessionId && runtime.isCurrent() ? [sessionId] : []
          })
        : undefined
    },
    getSelectedSessionId: () => $chatSessionId.get(),
    getAudio: id => runtimeForMessage(id)?.$chatMessageBodies.get()[id]?.replyAudio,
    getRecord: id => {
      const reference = voiceReference(id)

      return reference
        ? getVoicePlaybackStore(reference.sessionId).$voicePlaybackRecords.get()[reference.key]
        : undefined
    },
    getTarget: id => {
      const reference = voiceReference(id)
      const auth = $auth.get()

      return reference && reference.runtime.isCurrent() && auth.kind === 'authenticated'
        ? {
            authSessionId: auth.snapshot.sessionId,
            sessionId: reference.sessionId,
            messageId: reference.messageId,
            bubbleIndex: reference.bubbleIndex
          }
        : null
    },
    resolveId: ({ authSessionId, sessionId, messageId, bubbleIndex }) => {
      const auth = $auth.get()
      const runtime = findConversationRuntime(sessionId)

      if (auth.kind !== 'authenticated' || auth.snapshot.sessionId !== authSessionId || !runtime?.isCurrent()) {
        return undefined
      }

      const bodies = runtime.$chatMessageBodies.get()

      return runtime.$chatMessageList.get().find(item => {
        const body = bodies[item.id]

        return item.backendMessageId === messageId && body?.replyType === 'voice' && body.replyIndex === bubbleIndex
      })?.id
    },
    getFollowingTargets: id => {
      const runtime = runtimeForMessage(id)
      const sessionId = runtime?.$chatSessionId.get()
      const auth = $auth.get()

      if (!runtime?.isCurrent() || !sessionId || auth.kind !== 'authenticated') {
        return []
      }

      const list = runtime.$chatMessageList.get()
      const index = list.findIndex(item => item.id === id)

      if (index < 0) {
        return []
      }

      const bodies = runtime.$chatMessageBodies.get()
      const authSessionId = auth.snapshot.sessionId

      return list.slice(index + 1).flatMap(item => {
        const bubble = savedVoiceBubble(item.backendMessageId, bodies[item.id])

        if (!bubble) {
          return []
        }

        const { messageId, bubbleIndex } = bubble

        return [{ authSessionId, sessionId, messageId, bubbleIndex }]
      })
    },
    ready: id => voicePlaybackReady(voiceReference(id)?.sessionId ?? null),
    captureProgress: id => {
      const reference = voiceReference(id)

      if (!reference) {
        throw new Error('Voice message is not saved')
      }

      return captureVoiceProgress(reference.messageId, reference.bubbleIndex, reference.sessionId)
    },
    isAutoplay: () => $autoplayVoice.get(),
    retry: async id => {
      const reference = voiceReference(id)

      if (!reference) {
        throw new Error('Voice message is not saved')
      }

      const auth = $auth.get()

      const result = await requestResource('sessions.retry_voice_bubble', {
        path: { message_id: reference.messageId, bubble_index: reference.bubbleIndex }
      })

      const currentAuth = $auth.get()

      if (
        auth.kind === 'authenticated' &&
        currentAuth.kind === 'authenticated' &&
        currentAuth.snapshot.sessionId === auth.snapshot.sessionId &&
        reference.runtime.isCurrent() &&
        reference.runtime.$chatMessageBodies.get()[id] === reference.body
      ) {
        reference.runtime.updateVoiceBubble(reference.messageId, reference.bubbleIndex, result)
      }
    },
    setPlaying: setVoiceBarPlaying,
    setLoading: setVoiceBarLoading,
    setPaused: setVoiceBarPaused,
    setFailed: setVoiceBarFailed
  })
  setConversationVoiceSink({
    cancel: (sessionId, reason) => {
      if (reason === 'selection' && $conversationViews.get().length > 0) {
        return
      }

      const current = activeVoiceMessageId()

      if (!sessionId || !current || voiceReference(current)?.sessionId === sessionId) {
        cancelVoiceBar()
      }
    },
    enqueue: enqueueVoiceBars,
    setVisible: setVoiceSurfaceMounted,
    setRecording: setVoiceRecording
  })
  setVoiceBarControl({
    $autoplay: $autoplayVoice,
    restart: restartVoiceBar,
    setAutoplay: autoplayVoicePref.set,
    toggle: toggleVoiceBar
  })

  let disposed = false
  let viewUpdateQueued = false

  const listeners = [
    bindVoicePlaybackUpdates(),
    bindVoiceBarListeners(),
    $autoplayVoice.listen(refreshVoiceAutoplay),
    $chatSessionId.listen(refreshVoiceBarVisibility),
    $conversationViews.listen(() => {
      if (viewUpdateQueued) {
        return
      }

      viewUpdateQueued = true
      // 视图更新先注销再登记；等同一轮登记完成后判断，也覆盖主对话与轻语之间的交接。
      queueMicrotask(() => {
        viewUpdateQueued = false

        if (!disposed) {
          refreshVoiceBarVisibility()
        }
      })
    }),
    $gatewayState.listen(state => {
      if (state === 'closed' || state === 'error') {
        cancelVoiceBar()
      }
    })
  ]

  dispose = () => {
    disposed = true
    listeners.forEach(stop => stop())
  }
}
