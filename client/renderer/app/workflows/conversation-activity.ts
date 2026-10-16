import { $spriteState, setSpriteState } from '@/modules/character'
import {
  $chatSessionId,
  $companionSessionId,
  $conversationViews,
  type ConversationRuntime,
  conversationRuntimes
} from '@/modules/conversation'
import { $gatewayActiveToolCount } from '@/shared/store/gateway'

let stopListening: (() => void) | undefined

// 持续表现读取会话状态，瞬态和语音仍由 character 裁决。
export function syncConversationActivity(): void {
  const current = $spriteState.get()

  if (current !== 'idle' && current !== 'thinking' && current !== 'working') {
    return
  }

  const active = conversationRuntimes().filter(runtime => runtime.isCurrent() && runtime.$chatTurnInFlight.get())

  const working =
    $gatewayActiveToolCount.get() > 0 ||
    active.some(runtime => {
      const assistant = runtime.$chatMessageList.get().findLast(item => item.role === 'assistant')

      return assistant && Boolean(runtime.$chatMessageBodies.get()[assistant.id]?.toolName)
    })

  const next = working ? 'working' : active.length > 0 ? 'thinking' : 'idle'

  if (next !== current) {
    setSpriteState(next, { force: true })
  }
}

export function bindConversationActivity(): void {
  stopListening?.()
  const runtimes = new Map<ConversationRuntime, () => void>()

  const refresh = (): void => {
    const current = new Set(conversationRuntimes())

    for (const [runtime, stop] of runtimes) {
      if (!current.has(runtime)) {
        stop()
        runtimes.delete(runtime)
      }
    }

    for (const runtime of current) {
      if (!runtimes.has(runtime)) {
        runtimes.set(runtime, runtime.$chatTurnInFlight.listen(refresh))
      }
    }

    syncConversationActivity()
  }

  const stops = [
    $chatSessionId.listen(refresh),
    $companionSessionId.listen(refresh),
    $conversationViews.listen(refresh),
    $gatewayActiveToolCount.listen(syncConversationActivity),
    $spriteState.listen((state, previous) => {
      if (state === 'idle' || previous === 'speaking' || previous === 'emotional' || previous === 'interacting') {
        syncConversationActivity()
      }
    })
  ]

  stopListening = () => {
    stops.forEach(stop => stop())
    runtimes.forEach(stop => stop())
    runtimes.clear()
  }

  refresh()
}

function disposeConversationActivity(): void {
  stopListening?.()
  stopListening = undefined
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    disposeConversationActivity()
  })
}
