import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $actionsUi, exportActionsUiSnapshot, importActionsUiSnapshot } from './actions-state'

export const actionsUiHandoff: UiStateHandoff<'actions'> = {
  key: 'actions',
  revision: $actionsUi,
  capture: () => exportActionsUiSnapshot(),
  apply: snapshot => importActionsUiSnapshot(snapshot)
}
