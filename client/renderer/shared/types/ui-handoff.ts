import type { MainUiStateData } from '@ipc/contracts'

/** 跨窗口交接项：功能各自捕获与恢复自己的片段，应用层只按顺序组合。 */
export interface UiStateHandoff<Key extends keyof MainUiStateData> {
  key: Key
  revision: { listen: (listener: () => void) => () => void }
  capture: () => MainUiStateData[Key]
  apply: (snapshot: MainUiStateData[Key]) => Promise<void> | void
}
