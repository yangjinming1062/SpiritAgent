/** 动作模块类型：播放指令、回执、目录条目与视频包响应。IPC 契约类型仍归 shared/ipc；此处仅模块内类型。 */

export type NormalizedRect = readonly [left: number, top: number, right: number, bottom: number]

/** manifest 中单个动作片段（spiritagent.action.pack 的 clip）。 */
export interface ActionClipEntry {
  readonly action_id: number
  readonly asset_revision: number
  readonly system_slot: string
  readonly video_ref: string
  readonly duration_ms: number
  readonly frames: number
  readonly width: number
  readonly height: number
  readonly loopable: boolean
  readonly hitmask_ref: string | null
  readonly hitmask_grid: readonly [number, number] | null
  readonly hitmask_fps: number
  readonly peek_geometry?: PeekGeometry | null
  readonly content_rect?: NormalizedRect | null
}

export interface PeekGeometry {
  readonly side: 'left' | 'right'
  readonly cut_x: number
  readonly focus_rect: NormalizedRect
}

/** 目录 manifest（spiritagent.action.pack）。 */
export interface ActionCatalogManifest {
  readonly schema_version: 'spiritagent.action.pack'
  readonly pack_id: number
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
  readonly clip: ActionClipEntry
  readonly repeatCount: number
  readonly expiresAtMs: number | null
  /** 世代：每受理一条播放指令递增；旧实例回调凭它失效。 */
  readonly generation: number
}

/** 视频包中的单个动作。 */
export interface VideoActionWire {
  action: string
  name: string
  status: string
  stage: string
  error: string | null
  clip_url: string | null
  motion_prompt: string
  feedback?: string
  peek_geometry?: PeekGeometry | null
}

/** 视频包响应（`/api/companion/video-packs`：列表取 `packs`，generate / retry 返回单个包）。 */
export interface VideoPackWire {
  id: number
  pack_version: number
  outfit_id: number | null
  error: string | null
  actions: VideoActionWire[]
  status: string
  active: boolean
  identity_review: 'none' | 'pass' | 'review' | 'accepted'
  manifest_url: string | null
  can_retry: boolean
  can_regenerate: boolean
}
