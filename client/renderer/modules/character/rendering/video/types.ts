/** 视频动作包 manifest 客户端类型（对应 `spiritagent.video.pack/1`）。
 * 只含数据：不可变资源路径 + 内容哈希、画布与脚底锚点、动作映射与调度约束。 */

export interface VideoClipSpec {
  action: string
  /** companion-assets 裸路径；展示 URL 由客户端经主进程资产桥解析 */
  path: string
  sha256: string
  bytes: number
  frames: number
  duration_ms: number
  loop: boolean
  enter_pose: string | null
  exit_pose: string | null
  /** 逐帧 alpha 命中遮罩：[frame][row] 为位行（bit i = 第 i 列） */
  hitmask: number[][]
  hitmask_grid: [number, number] | null
  hitmask_fps: number
}

export interface VideoPackCanvas {
  width: number
  height: number
  fps: number
}

export interface VideoPackManifest {
  schema_version: 'spiritagent.video.pack/1'
  pack_id: number
  character_id: number | null
  appearance_id: number | null
  pack_version: number
  canvas: VideoPackCanvas
  clips: VideoClipSpec[]
  cover_path: string | null
  default_action: string
  walk_speed: number | null
  max_playback_rate: number
  mirror_allowed: boolean
  min_client_version: string
}
