import { requestResource } from '@/shared/lib/resource-request'
import type { PromptSubmissionRequest, PromptSubmissionResult } from '@protocol'

import { ApiError } from '../../../../shared/errors'

export type ConversationSubmission = PromptSubmissionRequest & { session_id: string }

export function submitConversationTurn(params: ConversationSubmission): Promise<PromptSubmissionResult> {
  const { session_id, ...body } = params

  return requestResource('sessions.submit_turn', { path: { session_id }, body })
}

export async function recoverConversationTurn(params: ConversationSubmission): Promise<PromptSubmissionResult> {
  return (await readConversationTurn(params.session_id, params.request_id)) ?? submitConversationTurn(params)
}

export async function readConversationTurn(
  sessionId: string,
  requestId: string
): Promise<PromptSubmissionResult | null> {
  try {
    return await requestResource('sessions.get_turn', {
      path: { session_id: sessionId, request_id: requestId }
    })
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) {
      throw error
    }

    return null
  }
}
