import { $chatVisible } from '@/shared/store/chat-visibility'
import { $presentation } from '@/shared/store/presentation'
import {
  $surfaceCompanion,
  $surfaceOpen,
  $surfaceRole,
  $surfaceScreenLocked,
  $surfaceSpriteVisible,
  isCompanionStageVisible
} from '@/shared/store/surfaces'

import { $screenLocked } from './activity'

/** 完整入口的对话面板不会遮住侧边伙伴；精灵旁轻语可见时暂停自主走位与主动投递。 */
export function isCharacterStageVisible(): boolean {
  return !$screenLocked.get() && isCompanionStageVisible() && ($surfaceRole.get() !== 'sprite' || !$chatVisible.get())
}

export function observeCharacterStageVisibility(listener: (visible: boolean) => void): () => void {
  const notify = (): void => listener(isCharacterStageVisible())

  const stops = [
    $screenLocked.listen(notify),
    $presentation.listen(notify),
    $chatVisible.listen(notify),
    $surfaceOpen.listen(notify),
    $surfaceCompanion.listen(notify),
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
