/** 动作模块类型：播放指令、回执、目录条目与动作包响应。IPC 契约类型仍归 shared/ipc；此处仅模块内类型。 */

export type NormalizedRect = readonly [left: number, top: number, right: number, bottom: number]

interface ActionClipBase {
  readonly action_id: number
  readonly asset_revision: number
  readonly system_slot: string
  readonly media_ref: string
  readonly width: number
  readonly height: number
  readonly hitmask_ref: string | null
  readonly hitmask_grid: readonly [number, number] | null
  readonly peek_geometry?: PeekGeometry | null
  readonly content_rect?: NormalizedRect | null
}

export interface ImageActionClipEntry extends ActionClipBase {
  readonly media_type: 'image'
}

export interface VideoActionClipEntry extends ActionClipBase {
  readonly media_type: 'video'
  readonly duration_ms: number
  readonly frames: number
  readonly loopable: boolean
  readonly hitmask_fps: number
}

/** 图片不携带播放时间和逐帧参数。 */
export type ActionClipEntry = ImageActionClipEntry | VideoActionClipEntry

export interface PeekGeometry {
  readonly side: 'left' | 'right'
  readonly cut_x: number
  readonly focus_rect: NormalizedRect
}

/** 动作目录 manifest。 */
export interface ActionCatalogManifest {
  readonly pack_id: number
  readonly outfit_id: number | null
  readonly catalog_version: number
  readonly canvas: { readonly width: number; readonly height: number }
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
  readonly clip: VideoActionClipEntry
  readonly repeatCount: number
  readonly expiresAtMs: number | null
  /** 世代：每受理一条播放指令递增；旧实例回调凭它失效。 */
  readonly generation: number
}

interface ActionWireBase {
  action: string
  name: string
  status: string
  stage: string
  error: string | null
  media_url: string | null
  feedback?: string
  peek_geometry?: PeekGeometry | null
}

export type ActionWire = ActionWireBase & ({ media_type: 'image' } | { media_type: 'video'; motion_prompt: string })

/** 动作包响应（`/api/companion/video-packs`：列表取 `packs`，generate / retry 返回单个包）。 */
export interface ActionPackWire {
  id: number
  pack_version: number
  outfit_id: number | null
  error: string | null
  actions: ActionWire[]
  status: string
  active: boolean
  identity_review: 'none' | 'pass' | 'review' | 'accepted'
  manifest_url: string | null
  can_retry: boolean
  can_regenerate: boolean
}
