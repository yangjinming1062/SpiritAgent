export { ChatMediaCard } from './chat-media-card'
export { ChatPanel } from './chat-panel'

export type { ConversationRuntime } from './chat-runtime'
export {
  $chatDraftFromUndo,
  $chatSessionId,
  $companionSessionId,
  $proactiveBubble,
  clearExternalAttachment,
  pushExternalAttachment,
  setChatSession,
  setCompanionSessionId,
  setProactiveBubble,
  showMediaHint
} from './chat-store'
export {
  conversationRuntimes,
  findConversationRuntime,
  getConversationRuntime,
  retainConversationRuntime,
  runtimeForMessage
} from './chat-store'
export { CompanionAvatar } from './companion-avatar'
export { ConversationInput } from './conversation-input'
export { $conversationInputBusy, beginConversationInputActivity } from './conversation-input-busy'
export { ConversationSurface } from './conversation-surface'
export {
  $conversationViews,
  $mainConversationDraftRevision,
  ConversationViewProvider,
  exportMainConversationDraftSnapshot,
  forgetConversationDraft,
  importMainConversationDraftSnapshot,
  isConversationActive,
  useConversationView
} from './conversation-view'

export { openWhisper } from './open-whisper'

export { consumePendingMessages, pendingMessages, rememberPendingMessage } from './pending-messages'
export { presetDisplayDescription, presetDisplayName, sessionDisplayTitle } from './preset-labels'
export {
  invalidateSessionHistory,
  loadLocalSessionHistory,
  rememberFullHistory,
  requestSessionSnapshot,
  SessionHistoryChangedError,
  syncSessionHistory
} from './session-history-cache'
export { archiveSession, createNewSession, deleteSession, pinSession, renameSession } from './sessions/actions'
export { onSessionListChanged } from './sessions/events'
export { fetchArchived, fetchSessions, runSessionSearch, setSessionSort } from './sessions/list'
export { fetchSystemPresets } from './sessions/presets'
export { ensureCompanionSession, openMainSession, restoreConversationSelection, switchSession } from './sessions/selection'
export {
  $archivedLoading,
  $archivedNextCursor,
  $archivedSessions,
  $archiveOpen,
  $currentSessionTitle,
  $searchLoading,
  $searchNextCursor,
  $searchResults,
  $sessionNavigationError,
  $sessions,
  $sessionSearch,
  $sessionsError,
  $sessionsLoading,
  $sessionsNextCursor,
  $sessionSort,
  $systemPresets,
  $systemPresetsFetched,
  $systemPresetsLoading,
  isCompanionSession,
  type SessionSort,
  TITLE_MAX_CHARS
} from './sessions/state'
export { onTopicFeedbackUpdated } from './topic-feedback-store'

export { conversationUiHandoff } from './ui-handoff'
export { useChatInput } from './use-chat-input'
export { useConversationUnread } from './use-conversation-unread'
export { useIsReadOnlySession } from './use-is-read-only-session'

export {
  activeVoiceMessageId,
  setConversationVoiceSink,
  setVoiceBarControl,
  setVoiceBarFailed,
  setVoiceBarLoading,
  setVoiceBarPaused,
  setVoiceBarPlaying
} from './voice-link'
export {
  bindVoicePlaybackUpdates,
  captureVoiceProgress,
  getVoicePlaybackStore,
  removeVoicePlayback,
  voicePlaybackReady
} from './voice-playback'
export type { MainConversationDraftSnapshot } from '@client-shared/ipc/ui-state'
