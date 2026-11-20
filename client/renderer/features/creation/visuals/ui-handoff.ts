import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $visualsUi, exportVisualsUiSnapshot, importVisualsUiSnapshot } from './visuals-state'

export const visualsUiHandoff: UiStateHandoff<'visuals'> = {
  revision: $visualsUi,
  capture: exportVisualsUiSnapshot,
  apply: importVisualsUiSnapshot
}
