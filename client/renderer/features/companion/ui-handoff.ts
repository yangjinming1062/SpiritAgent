import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import {
  $companionEditors,
  applyCompanionEditorUiSnapshot,
  captureCompanionEditorUiSnapshot
} from './companion-editor-state'

export const companionEditorsUiHandoff: UiStateHandoff<'companionEditors'> = {
  key: 'companionEditors',
  revision: $companionEditors,
  capture: () => captureCompanionEditorUiSnapshot(),
  apply: snapshot => applyCompanionEditorUiSnapshot(snapshot)
}
