/** 动作模块出口：目录、类型与播放运行时。 */

export {
  $activePlayInstance,
  acceptPlayCommand,
  reportReceipt,
  settlePlayInstance,
  shouldStartInstance
} from './action-runtime'
export {
  $actionCatalog,
  $actionCatalogStatus,
  actionCatalogChanged,
  ensurePeekAction,
  hydrateActionCatalog,
  resolveActionClipUrl,
  resolveHitmask
} from './action-store'
export type { ActionCatalogStatus, ActionHitmask, ActiveActionCatalog } from './action-store'
export type {
  ActionClipEntry,
  ActionPlaybackStatus,
  ActionPlayCommand,
  ActionPlayInstance,
  PeekGeometry
} from './action-types'
export { isActionStageVisible, observeActionStageVisibility } from './action-visibility'
