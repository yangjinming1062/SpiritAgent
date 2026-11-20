import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $lifeUi, exportLifeUiSnapshot, importLifeUiSnapshot } from './overview/life-state'

export const lifeUiHandoff: UiStateHandoff<'life'> = {
  revision: $lifeUi,
  capture: exportLifeUiSnapshot,
  apply: importLifeUiSnapshot
}
