import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import {
  $mainConversationDraftRevision,
  exportMainConversationDraftSnapshot,
  importMainConversationDraftSnapshot
} from './conversation-view'

export const conversationUiHandoff: UiStateHandoff<'conversation'> = {
  revision: $mainConversationDraftRevision,
  capture: exportMainConversationDraftSnapshot,
  apply: importMainConversationDraftSnapshot
}
