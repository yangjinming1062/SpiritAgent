import type { PromptSubmissionResult, SessionMessage } from '@protocol'

import type { GatewayEventPayloads } from '../../../../../shared/api.generated'
import type { createTurnCancellation, TurnReference } from '../../../../../shared/turn-cancellation'
import type { ChatMessageController } from '../chat-message-controller'
import type { ConversationSubmission } from '../chat-requests'
import type { ConversationState } from '../chat-runtime-state'
import type { PendingPromptItem } from '../chat-runtime-types'

/** 提交控制器各模块共享的只读装配输入。 */
export interface SubmissionBase {
  state: ConversationState
  messages: ChatMessageController
  isCurrent: () => boolean
  onReplyReplaced: (replacedMessageId: string, message: SessionMessage) => void
  settledRequests: Set<string>
}

/** 模块间协作面：模块只经这些显式能力调用同伴，装配顺序不影响调用。 */
export interface SubmissionPeers {
  cancellation: ReturnType<typeof createTurnCancellation>
  rememberSettled: (requestId: string) => void
  submitPendingBatch: () => void
  cancelPendingFlush: () => void
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
  cancellationRequested: (requestId: string) => boolean
  submissionFailed: (params: ConversationSubmission, error: unknown) => void
  isPromptUnconfirmed: (requestId: unknown) => boolean
}

/** 用户消息落库对齐与回合状态应用。 */
export interface PromptSubmissionProjection {
  bindTrailingUserMessageIds: (ids: string[], requestId?: string) => void
  preparePromptSubmission: SubmissionPeers['preparePromptSubmission']
  mergePersistedUserMessages: SubmissionPeers['mergePersistedUserMessages']
  applySessionState: SubmissionPeers['applySessionState']
  acceptPromptSubmission: SubmissionPeers['acceptPromptSubmission']
}

/** 连发队列与等待项。 */
export interface PendingQueueProjection {
  pushPendingPrompt: (item: PendingPromptItem) => void
  clearPendingPrompts: () => void
  schedulePendingFlush: () => void
  cancelPendingFlush: () => void
  submitPendingBatch: () => void
}

/** 回合归属、取消与回复替换。 */
export interface TurnControlProjection {
  cancellation: SubmissionPeers['cancellation']
  ownsTurn: (turn: TurnReference) => boolean
  refreshAfterCancellation: (turn: TurnReference) => Promise<void>
  cancellationRequested: (requestId: string) => boolean
  submissionFailed: (params: ConversationSubmission, error: unknown) => void
  stopActiveTurn: () => Promise<void>
  replaceAssistantReply: (messageId: string) => Promise<void>
  retryAssistantReply: (messageId: string) => Promise<void>
}

/** 未确认提交的恢复与回合事件接纳。 */
export interface TurnRecoveryProjection {
  recoverUnconfirmedSubmission: () => Promise<void>
  acceptsTurnEvent: (requestId?: string) => boolean
  isPromptUnconfirmed: (requestId: unknown) => boolean
  abandonDisconnectedTurn: () => void
}
