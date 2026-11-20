import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $diaryUi, exportDiaryUiSnapshot, importDiaryUiSnapshot } from './diary-state'

export const diaryUiHandoff: UiStateHandoff<'diary'> = {
  revision: $diaryUi,
  capture: exportDiaryUiSnapshot,
  apply: importDiaryUiSnapshot
}
