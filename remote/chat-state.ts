import type { GatewayEvent } from './api'
import { record } from './api'
import type { ActiveTurn, CompanionBubble, Resume, SessionMessage } from './types'

function emptyTurn(): ActiveTurn {
  return {
    request_id: '',
    origin_kind: 'remote',
    message_ids: [],
    text: '',
    reasoning: '',
    bubbles: [],
    tools: [],
    running: true
  }
}

function finishSegment(turn: ActiveTurn): ActiveTurn {
  return { ...turn, text: '', bubbles: turn.text ? [...turn.bubbles, { type: 'text', text: turn.text }] : turn.bubbles }
}

export function advanceTurn(previous: ActiveTurn | null, event: GatewayEvent): ActiveTurn | null {
  const payload = record(event.payload) ? event.payload : {}

  if (
    event.type === 'message.complete' ||
    event.type === 'error' ||
    (event.type === 'session.state' && payload.running === false)
  ) {
    return null
  }

  const turn = previous ?? emptyTurn()

  if (event.type === 'message.start') {
    const fresh =
      typeof payload.request_id === 'string' && turn.request_id && payload.request_id !== turn.request_id
        ? emptyTurn()
        : finishSegment(turn)

    return {
      ...fresh,
      request_id: typeof payload.request_id === 'string' ? payload.request_id : fresh.request_id,
      running: true
    }
  }

  if (event.type === 'message.break') {
    return finishSegment(turn)
  }

  if (event.type === 'message.delta' && typeof payload.text === 'string') {
    return { ...turn, text: turn.text + payload.text }
  }

  if (event.type === 'message.bubble' && record(payload.bubble) && typeof payload.bubble_index === 'number') {
    const bubbles = [...turn.bubbles]
    bubbles[payload.bubble_index] = payload.bubble as unknown as CompanionBubble

    return { ...turn, bubbles }
  }

  if (event.type === 'tool.start' || event.type === 'tool.complete') {
    if (!previous) {
      return null
    }
    const name = typeof payload.name === 'string' ? payload.name : '正在处理任务'
    const callId = typeof payload.call_id === 'string' ? payload.call_id : name

    return {
      ...turn,
      tools: [
        ...turn.tools.filter(tool => tool.call_id !== callId),
        { name, call_id: callId, status: event.type === 'tool.complete' ? 'complete' : 'running' }
      ]
    }
  }

  return previous
}

export function eventsAfterSnapshot(events: GatewayEvent[], snapshot: Resume): GatewayEvent[] {
  const requestId = snapshot.active_turn?.request_id ?? snapshot.last_submission?.request_id

  return events.filter(event => {
    if (event.seq === undefined || event.seq > (snapshot.current_seq ?? 0)) {
      return true
    }

    return (
      (event.type === 'error' || event.type === 'session.state') &&
      record(event.payload) &&
      event.payload.request_id === requestId
    )
  })
}

export function commandHistory(event: GatewayEvent): SessionMessage[] | null {
  if (event.type !== 'command.result' || !record(event.payload) || !record(event.payload.result)) {
    return null
  }

  const result = event.payload.result

  return result.status === 'ok' &&
    result.hydrate === true &&
    record(result.payload) &&
    Array.isArray(result.payload.messages)
    ? (result.payload.messages as SessionMessage[])
    : null
}

export function mergeMessages(previous: SessionMessage[], incoming: SessionMessage[]): SessionMessage[] {
  const messages = new Map(previous.map(message => [message.id, message]))
  incoming.forEach(message => messages.set(message.id, message))

  return [...messages.values()].sort((left, right) => (left.id ?? 0) - (right.id ?? 0))
}
