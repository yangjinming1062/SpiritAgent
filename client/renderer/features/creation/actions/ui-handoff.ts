import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $actionsUi, exportActionsUiSnapshot, importActionsUiSnapshot } from './actions-state'

export const actionsUiHandoff: UiStateHandoff<'actions'> = {
  revision: $actionsUi,
  capture: exportActionsUiSnapshot,
  apply: importActionsUiSnapshot
}
