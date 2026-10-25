import { compareResourceIds, isResourceId } from '../shared/resource-id'

import type { GatewayEvent } from './api'
import type { ActiveTurn, SessionMessage } from './types'

function emptyTurn(): ActiveTurn {
  return {
    request_id: '',
    origin_kind: 'remote',
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
        replace_reply_message_id: event.payload.replace_reply_message_id ?? null,
        request_id: event.payload.request_id ?? fresh.request_id,
        origin_kind: event.payload.origin_kind ?? fresh.origin_kind,
        running: true
      }
    }
    case 'message.break':
      return finishSegment(previous ?? emptyTurn())
    case 'message.delta': {
      const turn = previous ?? emptyTurn()
      return { ...turn, text: turn.text + event.payload.text }
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
