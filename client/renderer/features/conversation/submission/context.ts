import type { PromptSubmissionResult, SessionMessage } from '@protocol'

import type { GatewayEventPayloads } from '../../../../../shared/api.generated'
import type { createTurnCancellation, TurnReference } from '../../../../../shared/turn-cancellation'
import type { ChatMessageController } from '../chat-message-controller'
import type { ConversationSubmission } from '../chat-requests'
import type { ConversationState } from '../chat-runtime-state'
import type { ChatMessageListItem, PendingPromptItem } from '../chat-runtime-types'

/** 提交控制器各模块共享的上下文；跨模块调用经此对象，避免静态循环依赖。 */
export interface SubmissionContext {
  state: ConversationState
  messages: ChatMessageController
  isCurrent: () => boolean
  onReplyReplaced: (replacedMessageId: string, message: SessionMessage) => void
  settledRequests: Set<string>
  cancellation: ReturnType<typeof createTurnCancellation>
  rememberSettled: (requestId: string) => void

  bindTrailingUserMessageIds: (ids: string[], requestId?: string) => void
  preparePromptSubmission: (
    params: Omit<ConversationSubmission, 'request_id'>,
    bubbleIds?: string[]
  ) => ConversationSubmission
  mergePersistedUserMessages: (
    messages: SessionMessage[],
    requestId?: string,
    ids?: string[],
    trackActive?: boolean
  ) => void
  applySessionState: (event: Partial<GatewayEventPayloads['session.state']>) => void
  acceptPromptSubmission: (result: PromptSubmissionResult) => void
  recoverUnconfirmedSubmission: () => Promise<void>
  acceptsTurnEvent: (requestId?: string) => boolean
  isPromptUnconfirmed: (requestId: unknown) => boolean
  abandonDisconnectedTurn: () => void

  ownsTurn: (turn: TurnReference) => boolean
  refreshAfterCancellation: (turn: TurnReference) => Promise<void>
  cancellationRequested: (requestId: string) => boolean
  submissionFailed: (params: ConversationSubmission, error: unknown) => void
  stopActiveTurn: () => Promise<void>
  replaceAssistantReply: (messageId: string) => Promise<void>
  retryAssistantReply: (messageId: string) => Promise<void>

  pushPendingPrompt: (item: PendingPromptItem) => void
  clearPendingPrompts: () => void
  schedulePendingFlush: () => void
  cancelPendingFlush: () => void
  submitPendingBatch: () => void
}

export type { ChatMessageListItem }
