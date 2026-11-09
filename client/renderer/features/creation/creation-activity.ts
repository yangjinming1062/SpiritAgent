import { computed } from 'nanostores'

import type { CreationSection } from '@ipc/contracts'

import { $actionActivity, refreshActionActivity } from './actions'
import { $pendingScene, $sceneCreationIssue, $sceneRegenerating } from './environment'
import { $visualsJobs, refreshVisualJobs } from './visuals'

/** 活动条目的用户可读分组：进行中的制作与需要用户处理的事项。 */
type CreationActivityGroup = 'working' | 'attention'

export interface CreationActivityItem {
  id: string
  group: CreationActivityGroup
  /** 内部状态码原样保留，展示文案由消费方映射。 */
  state: string
  section: CreationSection
  /** 任务来源定位：动作或环境素材；影像工作项在影像页顶部集中展示。 */
  actionId?: string
  sceneId?: string
}

/**
 * 创作任务的统一投影：只合并各业务模块已有的状态，不新增状态源与轮询。
 * 状态归属仍在 visuals、actions 与 scene 模块；失败、结果未知与待复核
 * 归入“需要处理”，不与正常制作混在同一个进行中分组。
 */
export const $creationActivity = computed(
  [$actionActivity, $visualsJobs, $pendingScene, $sceneRegenerating, $sceneCreationIssue],
  (unlistedActions, jobs, pendingScene, regeneratingScene, creationIssue) => {
    const items: CreationActivityItem[] = []

    for (const job of jobs) {
      if (job.state === 'succeeded' || job.state === 'cancelled') {
        continue
      }

      const reviewPending = job.stage === 'review_pending'

      items.push({
        id: `visual:${job.id}`,
        group: reviewPending || job.state === 'failed' || job.state === 'unknown' ? 'attention' : 'working',
        state: reviewPending ? 'review_pending' : job.state,
        section: 'visuals'
      })
    }

    for (const action of unlistedActions) {
      const working = action.status === 'pending' || action.status === 'reviewing'

      items.push({
        id: `action:${action.id}`,
        group: working ? 'working' : 'attention',
        state: action.status,
        section: 'actions',
        actionId: action.id
      })
    }

    if (creationIssue?.outcome === 'unknown') {
      items.push({
        id: 'environment:creation-unknown',
        group: 'attention',
        state: 'creation_unknown',
        section: 'environment'
      })
    }

    const activeScene = pendingScene ?? regeneratingScene

    if (activeScene) {
      items.push({
        id: `environment:${activeScene.id}`,
        group: activeScene === pendingScene && activeScene.stage === 'waiting_upload' ? 'attention' : 'working',
        state: activeScene === pendingScene ? activeScene.stage || 'pending' : 'pending',
        section: 'environment',
        sceneId: activeScene.id
      })
    }

    return items
  }
)

/** 只有真正需要用户关注时才显示显著提醒；正常后台制作只保留适度的进行状态。 */
export const $creationAttention = computed($creationActivity, items => items.some(item => item.group === 'attention'))

/** 登录与重连后的任务摘要补读：只触发各模块已有读取入口，失败静默等待事件或页面读取。 */
export async function hydrateCreationActivity(): Promise<void> {
  await Promise.allSettled([refreshVisualJobs(), refreshActionActivity()])
}
