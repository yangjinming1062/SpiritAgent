import { currentClearEpoch } from '@/shared/lib/storage'

import { createChatHistoryController } from './chat-history-controller'
import { createChatMessageController } from './chat-message-controller'
import { createConversationState } from './chat-runtime-state'
import { createChatSubmissionController } from './chat-submission-controller'
import { releaseVoicePlaybackStore } from './voice-playback'

export function createConversationRuntime(sessionId: string | null) {
  const epoch = currentClearEpoch()
  let alive = true
  const isCurrent = (): boolean => alive && epoch === currentClearEpoch()
  const state = createConversationState(sessionId)
  const messages = createChatMessageController(state)

  const submission = createChatSubmissionController(state, messages, isCurrent, (messageId, message) => {
    history.replaceAssistantReplyMessage(messageId, message)
  })

  const history = createChatHistoryController(state, messages, submission, isCurrent)
  const { $runtimeRevision, $chatMessageList, $chatMessageBodies, $chatTurnInFlight } = state.stores

  const revise = (): void => $runtimeRevision.set($runtimeRevision.get() + 1)

  const revisionListeners = [
    $chatMessageList.listen(revise),
    $chatMessageBodies.listen(revise),
    $chatTurnInFlight.listen(revise)
  ]

  const dispose = (): void => {
    alive = false
    submission.disposeSubmission()
    revisionListeners.forEach(stop => stop())
    releaseVoicePlaybackStore(sessionId)
  }

  return { ...state.stores, ...messages.api, ...submission, ...history, isCurrent, dispose }
}

export type ConversationRuntime = ReturnType<typeof createConversationRuntime>
