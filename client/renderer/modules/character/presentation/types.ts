/** 角色形象的渲染方式枚举与呈现层公共类型。
 * 偏好模式、当前有效渲染器与资源就绪状态分别维护；偏好不等于资产就绪。 */
export type CharacterRenderMode = 'model' | 'video'

/** 视频动作包的必需动作键；渲染器按资产实际支持兑现，缺素材回退 idle。 */
export type VideoActionKey = 'idle' | 'walk_left' | 'walk_right' | 'drag'

/** 桌面实际挂载的渲染层：视频链、模型链或通用兜底（程序化蛋）。 */
export type CompanionRendererKind = 'video' | 'model' | 'fallback'

export interface CompanionPresentation {
  readonly mode: CharacterRenderMode
  readonly renderer: CompanionRendererKind
  /** 视频动作包是否就绪；未就绪时按兜底顺序呈现，不伪造视频资源。 */
  readonly videoReady: boolean
}
