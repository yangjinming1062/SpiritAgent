import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $sceneUi, exportSceneUiSnapshot, importSceneUiSnapshot } from './scene-ui-state'

export const sceneUiHandoff: UiStateHandoff<'scene'> = {
  revision: $sceneUi,
  capture: exportSceneUiSnapshot,
  apply: importSceneUiSnapshot
}
