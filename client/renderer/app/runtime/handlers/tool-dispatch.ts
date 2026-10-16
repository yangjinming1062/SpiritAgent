import { syncConversationActivity } from '@/app/workflows/conversation-activity'
import type { ConversationRuntime } from '@/modules/conversation'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { getStrings } from '@/shared/strings'

import { decodePayload } from '../gateway-event-util'

export function handleToolStart(event: GatewayEvent, runtime: ConversationRuntime): void {
  const p = decodePayload<{ name?: string }>(event.payload)

  runtime.setAssistantTool(p.name ?? getStrings().chat.tools.genericName)
  syncConversationActivity()
}

export function handleToolComplete(runtime: ConversationRuntime): void {
  runtime.setAssistantTool(null)
  syncConversationActivity()
}
