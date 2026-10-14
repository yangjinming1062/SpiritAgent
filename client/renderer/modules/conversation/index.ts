export { chatDisplayText } from './chat-display-text'

export { ChatMediaCard } from './chat-media-card'
export { ChatPanel } from './chat-panel'

export type { ConversationRuntime } from './chat-runtime'
export {
  $chatDraftFromUndo,
  $chatSessionId,
  $companionSessionId,
  $proactiveBubble,
  type ChatMessageBody,
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
export { consumePendingMessages, pendingMessages, rememberPendingMessage } from './pending-messages'

export { presetDisplayDescription, presetDisplayName, sessionDisplayTitle } from './preset-labels'

export {
  invalidateSessionHistory,
  loadLocalSessionHistory,
  rememberFullHistory,
  SessionHistoryChangedError,
  syncSessionHistory
} from './session-history-cache'
export {
  $archivedLoading,
  $archivedSessions,
  $archiveOpen,
  $currentSessionTitle,
  $searchLoading,
  $searchResults,
  $sessionNavigationError,
  $sessions,
  $sessionSearch,
  $sessionsError,
  $sessionsLoading,
  $sessionSort,
  $systemPresets,
  $systemPresetsFetched,
  $systemPresetsLoading,
  archiveSession,
  createNewSession,
  deleteSession,
  ensureCompanionSession,
  fetchArchived,
  fetchSessions,
  fetchSystemPresets,
  isCompanionSession,
  onSessionListChanged,
  openMainSession,
  pinSession,
  renameSession,
  restoreConversationSelection,
  runSessionSearch,
  type SessionSort,
  setSessionSort,
  switchSession,
  TITLE_MAX_CHARS
} from './session-list-store'
export { useChatInput } from './use-chat-input'
export { useConversationUnread } from './use-conversation-unread'

export { useIsReadOnlySession } from './use-is-read-only-session'
export {
  $voiceBarPlayingId,
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
