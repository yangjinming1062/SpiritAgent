import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import {
  $mainConversationDraftRevision,
  exportMainConversationDraftSnapshot,
  importMainConversationDraftSnapshot
} from './conversation-view'

export const conversationUiHandoff: UiStateHandoff<'conversation'> = {
  key: 'conversation',
  revision: $mainConversationDraftRevision,
  capture: () => exportMainConversationDraftSnapshot(),
  apply: snapshot => importMainConversationDraftSnapshot(snapshot)
}
