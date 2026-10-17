import { syncConversationActivity } from '@/app/workflows/conversation-activity'
import type { ConversationRuntime } from '@/modules/conversation'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { getStrings } from '@/shared/strings'

export function handleToolStart(
  event: Extract<GatewayEvent, { type: 'tool.start' }>,
  runtime: ConversationRuntime
): void {
  runtime.setAssistantTool(event.payload.name ?? getStrings().chat.tools.genericName)
  syncConversationActivity()
}

export function handleToolComplete(runtime: ConversationRuntime): void {
  runtime.setAssistantTool(null)
  syncConversationActivity()
}
