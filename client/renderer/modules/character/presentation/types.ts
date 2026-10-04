/** 角色形象呈现层公共类型。动作素材支持图片与视频；资源就绪时呈现素材，否则落兜底（蛋形）。 */

/** 基础状态机的系统动作键（与后端 `SYSTEM_SLOTS` 对应）。动态动作不进入基础状态机：以数据化表达请求（play_id + epoch）经 actions 模块下发，由 MediaStage 消费统一调度器结果。 */
export const SYSTEM_ACTION_KEYS = ['idle', 'walk_left', 'walk_right', 'drag', 'peek_left', 'peek_right'] as const
export type SystemActionKey = (typeof SYSTEM_ACTION_KEYS)[number]

/** 蛋形兜底状态：区分尚未就绪与生成失败。 */
export type CompanionFallbackStatus = 'preparing' | 'generating' | 'failed' | 'unavailable'

export type CompanionPresentation =
  | { readonly renderer: 'media' }
  | {
      readonly renderer: 'fallback'
      readonly fallbackStatus: CompanionFallbackStatus
      readonly fallbackMessage: string
      readonly fallbackActionAvailable: boolean
    }
