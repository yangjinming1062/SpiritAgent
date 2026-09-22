/** 角色形象呈现层公共类型。视频链是唯一渲染方式；资源就绪时挂视频，否则落兜底（蛋形）。 */

/** 已知动作键（与后端 `VIDEO_ACTION_KEYS` 对应）；缺素材时回退 idle，可按需补齐。 */
export const VIDEO_ACTION_KEYS = ['idle', 'walk_left', 'walk_right', 'drag'] as const
export type VideoActionKey = (typeof VIDEO_ACTION_KEYS)[number]

/** 桌面实际挂载的渲染层：视频链或通用兜底（程序化蛋）。 */
export type CompanionRendererKind = 'video' | 'fallback'

export interface CompanionPresentation {
  readonly renderer: CompanionRendererKind
  /** 视频动作包是否就绪；未就绪时按兜底顺序呈现，不伪造视频资源。 */
  readonly videoReady: boolean
}
