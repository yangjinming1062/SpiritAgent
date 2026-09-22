/** 动作模块出口：目录、类型与播放运行时。 */

export {
  $activePlayInstance,
  acceptPlayCommand,
  currentAppearanceEpoch,
  finishPlayInstance,
  isInstanceCurrent,
  nextAppearanceEpoch,
  reportReceipt,
  shouldStartInstance
} from './action-runtime'
export {
  $actionCatalog,
  $actionCatalogStatus,
  actionCatalogChanged,
  clipKey,
  hydrateActionCatalog,
  resolveActionClipUrl,
  resolveHitmask
} from './action-store'
export type { ActionCatalogStatus, ActionHitmask, ActiveActionCatalog } from './action-store'
export type { ActionPlaybackStatus, ActionPlayCommand, ActionPlayInstance } from './action-types'
