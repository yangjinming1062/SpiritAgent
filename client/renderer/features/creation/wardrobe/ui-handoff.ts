import type { UiStateHandoff } from '@/shared/types/ui-handoff'

import { $appearanceUi, exportAppearanceUiSnapshot, importAppearanceUiSnapshot } from './appearance-ui-state'
import { $designSessions, applyDesignSessionSnapshot, readDesignSessionSnapshot } from './design-session'

export const appearanceUiHandoff: UiStateHandoff<'appearance'> = {
  key: 'appearance',
  revision: $appearanceUi,
  capture: () => exportAppearanceUiSnapshot(),
  apply: snapshot => importAppearanceUiSnapshot(snapshot)
}

// 制作会话按草稿整体交接，草稿 ID 由上面的造型片段先恢复。
export const wardrobeDesignUiHandoff: UiStateHandoff<'wardrobeDesign'> = {
  key: 'wardrobeDesign',
  revision: $designSessions,
  capture: () => readDesignSessionSnapshot(),
  apply: snapshot => applyDesignSessionSnapshot(snapshot)
}
