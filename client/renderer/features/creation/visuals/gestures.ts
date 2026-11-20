import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'
import { $presentation } from '@/shared/store/presentation'

import type { ApiSchemas } from '../../../../../shared/api.generated'

import { enqueueInteraction } from './director'

const GROUP_MS = 400
const GESTURE_TTL_MS = 15_000
type Gesture = ApiSchemas['GestureReactionRequest']['kind']

interface PendingGesture {
  kind: Gesture
  count: number
  at: number
  stageEpoch: number
  isCurrent: () => boolean
  conversationId?: string
}

let pending: PendingGesture[] = []
let timer: ReturnType<typeof setTimeout> | null = null

function resetGesture(): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }

  pending = []
}

registerStorageClearHandler(resetGesture)

function eligible(gesture: PendingGesture): boolean {
  const stage = $presentation.get()

  return (
    gesture.isCurrent() &&
    $gatewayState.get() === 'open' &&
    (gesture.kind === 'chat_opened' || (stage.stageVisible && stage.stageEpoch === gesture.stageEpoch)) &&
    Date.now() - gesture.at < GESTURE_TTL_MS
  )
}

async function dispatchGesture(gesture: PendingGesture): Promise<void> {
  if (!eligible(gesture)) {
    return
  }

  const requestId = crypto.randomUUID()

  const result = await authedResource('companion.react_to_gesture', {
    body: {
      request_id: requestId,
      kind: gesture.kind,
      conversation_id: gesture.conversationId,
      occurred_at: new Date(gesture.at).toISOString(),
      count: gesture.count
    }
  })

  if (!apiSucceeded(result, 'companion-gesture', 'Gesture decision failed') || !eligible(gesture)) {
    return
  }

  if (result.value?.visual) {
    enqueueInteraction(result.value.visual, requestId, gesture.at)
  }
}

/** 合并一次连续手势；换账号或换舞台后丢弃迟到的表现。 */
export function enqueueGesture(kind: Gesture, conversationId?: string): void {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return
  }

  const stage = $presentation.get()
  const previous = pending.at(-1)

  if (
    previous?.kind === kind &&
    previous.stageEpoch === stage.stageEpoch &&
    previous.conversationId === conversationId &&
    previous.isCurrent()
  ) {
    previous.count = Math.min(10, previous.count + 1)
    previous.at = Date.now()
  } else {
    pending.push({ kind, conversationId, at: Date.now(), count: 1, isCurrent, stageEpoch: stage.stageEpoch })
  }

  if (timer === null) {
    timer = setTimeout(async () => {
      const batch = pending
      resetGesture()

      for (const gesture of batch) {
        await dispatchGesture(gesture)
      }
    }, GROUP_MS)
  }
}
