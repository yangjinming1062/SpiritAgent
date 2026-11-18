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
  speak?: (text: string) => Promise<void>
}

let pending: PendingGesture | null = null
let timer: ReturnType<typeof setTimeout> | null = null

function resetGesture(): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }

  pending = null
}

registerStorageClearHandler(resetGesture)

function eligible(gesture: PendingGesture): boolean {
  const stage = $presentation.get()

  return (
    gesture.isCurrent() &&
    $gatewayState.get() === 'open' &&
    stage.stageVisible &&
    stage.stageEpoch === gesture.stageEpoch &&
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

  if (result.value?.text) {
    await gesture.speak?.(result.value.text)
  }
}

/** 合并一次连续手势；换账号或换舞台后丢弃迟到的表现。 */
export function enqueueGesture(kind: Gesture, speak?: (text: string) => Promise<void>): void {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return
  }

  const stage = $presentation.get()
  const previous = pending
  const count = previous?.kind === kind && previous.stageEpoch === stage.stageEpoch ? previous.count + 1 : 1
  pending = { at: Date.now(), count: Math.min(10, count), isCurrent, kind, speak, stageEpoch: stage.stageEpoch }

  if (timer === null) {
    timer = setTimeout(() => {
      const gesture = pending
      resetGesture()

      if (gesture) {
        void dispatchGesture(gesture)
      }
    }, GROUP_MS)
  }
}
