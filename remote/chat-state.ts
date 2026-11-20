import type { HttpCallOptions } from '../shared/api'
import { isRecord } from '../shared/is-record'
import { compareResourceIds, isResourceId } from '../shared/resource-id'
import type { TurnReference } from '../shared/turn-cancellation'

import { ApiError, type GatewayEvent, type RemoteApi } from './api'
import type { ActiveTurn, ChatAttachment, RemoteSession, SessionMessage } from './types'

function emptyTurn(): ActiveTurn {
  return {
    request_id: '',
    message_ids: [],
    assistant_message_id: null,
    messages: [],
    replace_reply_message_id: null,
    text: '',
    reasoning: '',
    bubbles: [],
    tools: [],
    running: true
  }
}

function finishSegment(turn: ActiveTurn): ActiveTurn {
  return turn.text ? { ...turn, text: '', bubbles: [...turn.bubbles, { type: 'text', text: turn.text }] } : turn
}

export function advanceTurn(previous: ActiveTurn | null, event: GatewayEvent): ActiveTurn | null {
  switch (event.type) {
    case 'message.complete':
    case 'error':
      return null
    case 'session.state':
      return event.payload.running ? previous : null
    case 'message.start': {
      const turn = previous ?? emptyTurn()
      const fresh =
        event.payload.request_id && turn.request_id && event.payload.request_id !== turn.request_id
          ? emptyTurn()
          : finishSegment(turn)
      return {
        ...fresh,
        replace_reply_message_id: event.payload.replace_reply_message_id,
        request_id: event.payload.request_id ?? fresh.request_id,
        running: true
      }
    }
    case 'message.break':
      return finishSegment(previous ?? emptyTurn())
    case 'message.delta': {
      const turn = previous ?? emptyTurn()
      return { ...turn, text: turn.text + event.payload.text }
    }
    case 'message.reasoning.delta': {
      const turn = previous ?? emptyTurn()
      return { ...turn, reasoning: turn.reasoning + event.payload.text }
    }
    case 'message.bubble': {
      const turn = previous ?? emptyTurn()
      const bubbles = [...turn.bubbles]
      bubbles[event.payload.bubble_index] = event.payload.bubble
      return { ...turn, assistant_message_id: event.payload.message_id, bubbles }
    }
    case 'tool.start':
    case 'tool.complete': {
      if (!previous) {
        return null
      }
      const name = event.payload.name ?? '正在处理任务'
      const callId = event.payload.call_id ?? name
      return {
        ...previous,
        tools: [
          ...previous.tools.filter(tool => tool.call_id !== callId),
          { name, call_id: callId, status: event.type === 'tool.complete' ? 'complete' : 'running' }
        ]
      }
    }
    default:
      return previous
  }
}

export function commandHistory(event: GatewayEvent): SessionMessage[] | null {
  if (event.type !== 'command.result') {
    return null
  }

  const result = event.payload.result

  return result.status === 'ok' && result.hydrate === true && result.payload && 'messages' in result.payload
    ? result.payload.messages
    : null
}

export function mergeMessages(previous: SessionMessage[], incoming: SessionMessage[]): SessionMessage[] {
  const messages = new Map(previous.map(message => [message.id, message]))
  incoming.forEach(message => messages.set(message.id, message))

  return [...messages.values()].sort((left, right) => compareResourceIds(left.id, right.id))
}

export function activeReplyIsPersisted(turn: ActiveTurn | null, messages: SessionMessage[]): boolean {
  return (
    isResourceId(turn?.assistant_message_id) &&
    messages.some(
      message =>
        message.id === turn.assistant_message_id &&
        message.role === 'assistant' &&
        message.content_type === 'companion_reply'
    )
  )
}

export interface PendingPrompt {
  id: string
  text: string
  attachments: ChatAttachment[]
  retry?: string
  replace?: string
}

export function readPendingPrompt(key: string): PendingPrompt | null {
  const stored = sessionStorage.getItem(key)

  if (stored === null) {
    return null
  }

  const value: unknown = JSON.parse(stored)

  if (
    !isRecord(value) ||
    typeof value.id !== 'string' ||
    typeof value.text !== 'string' ||
    !Array.isArray(value.attachments) ||
    (value.retry !== undefined && !isResourceId(value.retry)) ||
    (value.replace !== undefined && !isResourceId(value.replace))
  ) {
    throw new Error('待确认的消息记录无法读取，请核对会话后重新登录')
  }

  const attachments: ChatAttachment[] = value.attachments.map((attachment: unknown) => {
    if (
      !isRecord(attachment) ||
      (attachment.type !== 'image' && attachment.type !== 'video' && attachment.type !== 'audio') ||
      typeof attachment.url !== 'string'
    ) {
      throw new Error('待确认的消息附件无法读取，请核对会话后重新登录')
    }

    return { type: attachment.type, url: attachment.url }
  })

  return {
    id: value.id,
    text: value.text,
    attachments,
    retry: isResourceId(value.retry) ? value.retry : undefined,
    replace: isResourceId(value.replace) ? value.replace : undefined
  }
}

export async function readTurnReceipt(api: RemoteApi, turn: TurnReference, options?: HttpCallOptions) {
  try {
    return await api.invoke(
      'sessions.get_turn',
      { path: { session_id: turn.sessionId, request_id: turn.requestId } },
      options
    )
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null
    }
    throw error
  }
}

export function clearChatState(account: RemoteSession): void {
  const prefix = `remote:${account.user.id}:${account.device.id}:`
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith(prefix + 'submission:') || key.startsWith(prefix + 'cancellation:')) {
        sessionStorage.removeItem(key)
      }
    }
  } catch {
    // 存储受限不能阻止撤销连接和释放录音。
  }
}
