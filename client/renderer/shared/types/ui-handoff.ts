import type { MainUiStateData } from '@ipc/contracts'

/** 跨窗口交接项：功能同步捕获与恢复自己的片段，在同一账户代次内完成交接。 */
export interface UiStateHandoff<Key extends keyof MainUiStateData> {
  revision: { listen: (listener: () => void) => () => void }
  capture: () => MainUiStateData[Key]
  apply: (snapshot: MainUiStateData[Key]) => void
}
