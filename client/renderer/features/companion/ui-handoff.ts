import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import {
  $companionEditors,
  applyCompanionEditorUiSnapshot,
  captureCompanionEditorUiSnapshot
} from './companion-editor-state'

export const companionEditorsUiHandoff: UiStateHandoff<'companionEditors'> = {
  revision: $companionEditors,
  capture: captureCompanionEditorUiSnapshot,
  apply: applyCompanionEditorUiSnapshot
}
