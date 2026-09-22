/** 动作模块类型：播放指令、回执与目录条目。
 * IPC 契约类型仍归 shared/ipc；此处仅模块内类型。 */

/** manifest 中单个动作片段（spiritagent.action.pack 的 clip）。 */
export interface ActionClipEntry {
  readonly action_id: number
  readonly asset_revision: number
  readonly system_slot: string
  readonly video_ref: string
  readonly duration_ms: number
  readonly frames: number
  readonly loopable: boolean
  readonly enter_pose: string | null
  readonly exit_pose: string | null
  readonly hitmask_ref: string | null
  readonly hitmask_grid: readonly [number, number] | null
  readonly hitmask_fps: number
}

/** 目录 manifest（spiritagent.action.pack）。 */
export interface ActionCatalogManifest {
  readonly schema_version: 'spiritagent.action.pack'
  readonly pack_id: number
  readonly character_id: number | null
  readonly outfit_id: number | null
  readonly catalog_version: number
  readonly canvas: { readonly width: number; readonly height: number; readonly fps: number }
  readonly clips: readonly ActionClipEntry[]
  readonly cover_path: string | null
  readonly default_action: string
}

/** 播放指令（companion.action.play_requested 载荷）。 */
export interface ActionPlayCommand {
  readonly play_id: string
  readonly target_device: string
  readonly target_surface: string
  readonly pack_id: number
  readonly appearance_epoch: number
  readonly action_id: number
  readonly asset_revision_id: number | null
  readonly repeat_count: number
  readonly expires_at: string | null
  readonly source: string
}

/** 播放回执状态；started 在角色真实可见后上报。 */
export type ActionPlaybackStatus = 'started' | 'completed' | 'interrupted' | 'rejected'

/** 统一调度器裁决后的播放实例。 */
export interface ActionPlayInstance {
  readonly playId: string
  readonly packId: number
  readonly appearanceEpoch: number
  readonly actionId: number
  readonly assetRevisionId: number | null
  readonly clip: ActionClipEntry
  readonly repeatCount: number
  readonly expiresAtMs: number | null
  /** 世代：外观切换递增；旧实例回调凭它失效。 */
  readonly generation: number
}
