import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $lifeUi, exportLifeUiSnapshot, importLifeUiSnapshot } from './overview/life-state'

export const lifeUiHandoff = {
  revision: $lifeUi,
  capture: exportLifeUiSnapshot,
  apply: importLifeUiSnapshot
} satisfies UiStateHandoff<'life'>
