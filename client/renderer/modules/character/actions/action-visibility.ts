import { $chatVisible } from '@/shared/store/chat-visibility'
import { $presentation } from '@/shared/store/presentation'
import {
  $surfaceCompanions,
  $surfaceOpen,
  $surfaceRole,
  $surfaceScreenLocked,
  $surfaceSpriteVisible,
  isCompanionStageVisible
} from '@/shared/store/surfaces'

import { $screenLocked } from '../activity'

/** 完整入口的对话面板不会遮住侧边伙伴；桌面轻语仍会暂停表达。 */
export function isActionStageVisible(): boolean {
  return !$screenLocked.get() && isCompanionStageVisible() && ($surfaceRole.get() !== 'sprite' || !$chatVisible.get())
}

export function observeActionStageVisibility(listener: (visible: boolean) => void): () => void {
  const notify = (): void => listener(isActionStageVisible())

  const stops = [
    $screenLocked.listen(notify),
    $presentation.listen(notify),
    $chatVisible.listen(notify),
    $surfaceOpen.listen(notify),
    $surfaceCompanions.listen(notify),
    $surfaceRole.listen(notify),
    $surfaceScreenLocked.listen(notify),
    $surfaceSpriteVisible.listen(notify)
  ]

  document.addEventListener('visibilitychange', notify)
  notify()

  return () => {
    stops.forEach(stop => stop())
    document.removeEventListener('visibilitychange', notify)
  }
}
