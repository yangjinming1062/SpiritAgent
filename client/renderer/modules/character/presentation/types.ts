/** 角色形象呈现层公共类型。视频链是唯一渲染方式；资源就绪时挂视频，否则落兜底（蛋形）。 */

/** 基础状态机的系统动作键（与后端 `SYSTEM_SLOTS` 对应）。动态动作不进入基础状态机：以数据化表达请求（play_id + epoch）经 actions 模块下发，由 VideoStage 消费统一调度器结果。 */
export const VIDEO_ACTION_KEYS = ['idle', 'walk_left', 'walk_right', 'drag', 'peek_left', 'peek_right'] as const
export type VideoActionKey = (typeof VIDEO_ACTION_KEYS)[number]

/** 桌面实际挂载的渲染层：视频链或通用兜底（程序化蛋）。 */
export type CompanionRendererKind = 'video' | 'fallback'

/** 蛋形兜底状态：区分尚未就绪与生成失败。 */
export type CompanionFallbackStatus = 'preparing' | 'generating' | 'failed' | 'unavailable'

export type CompanionPresentation =
  | { readonly renderer: 'video' }
  | {
      readonly renderer: 'fallback'
      readonly fallbackStatus: CompanionFallbackStatus
      readonly fallbackMessage: string
      readonly fallbackActionAvailable: boolean
    }
