import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $lifeUi, exportLifeUiSnapshot, importLifeUiSnapshot } from './overview/life-state'

export const lifeUiHandoff: UiStateHandoff<'life'> = {
  key: 'life',
  revision: $lifeUi,
  capture: () => exportLifeUiSnapshot(),
  apply: snapshot => importLifeUiSnapshot(snapshot)
}
