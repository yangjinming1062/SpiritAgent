import { useStore } from '@nanostores/react'
import { computed } from 'nanostores'
import { useMemo } from 'react'

import { voicePlaybackKey } from '@ipc/contracts'

import { getConversationRuntime } from './chat-store'
import { pendingMessages } from './pending-messages'
import { getVoicePlaybackStore } from './voice-playback'

// 查看消息和听完语音是两个独立状态；先取得收听快照，避免重启时把已听语音短暂报成未听。
export function useConversationUnread(sessionId: string | null) {
  const runtime = getConversationRuntime(sessionId)
  const playback = getVoicePlaybackStore(sessionId)

  const $firstUnheard = useMemo(
    () =>
      computed(
        [
          runtime.$chatMessageList,
          runtime.$chatMessageBodies,
          playback.$voicePlaybackRecords,
          playback.$voicePlaybackLoaded
        ],
        (list, bodies, records, loaded) => {
          if (!sessionId || !loaded) {
            return null
          }

          for (const item of list) {
            const body = bodies[item.id]

            // 空音频已降级为文字，不制造无法通过播放解除的提醒。
            if (
              item.role === 'assistant' &&
              item.backendMessageId !== undefined &&
              body?.replyType === 'voice' &&
              body.replyAudio &&
              body.replyIndex !== undefined
            ) {
              const key = voicePlaybackKey(item.backendMessageId, body.replyIndex)

              if (records[key]?.listened !== true) {
                return key
              }
            }
          }

          return null
        }
      ),
    [runtime, playback, sessionId]
  )

  const $hasPending = useMemo(
    () => computed(pendingMessages.$atom, messages => messages.some(message => message.sessionId === sessionId)),
    [sessionId]
  )

  const firstUnheard = useStore($firstUnheard)
  const hasPending = useStore($hasPending)

  return { hasUnread: hasPending || firstUnheard !== null, firstUnheard }
}
