import { $chatSessionId, setChatSession } from '../chat-store'
import { forgetSessionHistory } from '../session-history-cache'
import { removeVoicePlayback } from '../voice-playback'

import { fetchArchived, fetchSessions, runSessionSearch } from './list'
import { openMainSession } from './selection'
import { $archiveOpen, $sessionSearch } from './state'

/** 删除、归档与列表变更事件：清理所属缓存，并按当前视图刷新可见列表。 */
export function onSessionListChanged(payload: { session_id?: string; deleted?: boolean }): void {
  if (payload.deleted && payload.session_id) {
    forgetSessionHistory(payload.session_id)
    removeVoicePlayback(payload.session_id)

    if ($chatSessionId.get() === payload.session_id) {
      setChatSession(null)
      void openMainSession()
    }
  }

  void fetchSessions()

  if ($archiveOpen.get()) {
    void fetchArchived()
  }

  if ($sessionSearch.get().trim()) {
    void runSessionSearch($sessionSearch.get())
  }
}
