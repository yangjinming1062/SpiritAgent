/** 角色形象呈现层公共类型。视频链是唯一渲染方式；资源就绪时挂视频，否则落兜底（蛋形）。 */

/** 视频动作包的必需动作键；渲染器按资产实际支持兑现，缺素材回退 idle。 */
export type VideoActionKey = 'idle' | 'walk_left' | 'walk_right' | 'drag'

/** 桌面实际挂载的渲染层：视频链或通用兜底（程序化蛋）。 */
export type CompanionRendererKind = 'video' | 'fallback'

export interface CompanionPresentation {
  readonly renderer: CompanionRendererKind
  /** 视频动作包是否就绪；未就绪时按兜底顺序呈现，不伪造视频资源。 */
  readonly videoReady: boolean
}
