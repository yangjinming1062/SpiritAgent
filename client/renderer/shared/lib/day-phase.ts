/** 本地时段的环境氛围色调：纯客户端演出，不创建生活经历、不推进生活系统。 */

export type DayPhase = 'night' | 'dawn' | 'day' | 'dusk'

const PHASE_FILTERS: Record<DayPhase, string> = {
  night: 'brightness(0.72) saturate(0.85) hue-rotate(-8deg)',
  dawn: 'brightness(0.92) saturate(1.02) hue-rotate(-4deg)',
  day: 'none',
  dusk: 'brightness(0.88) saturate(1.06) hue-rotate(6deg)'
}

/** 按小时粗分时段；边界重叠时偏向更亮的档位，避免频闪切换。 */
export function dayPhaseAt(hour: number): DayPhase {
  if (hour >= 7 && hour < 17) {
    return 'day'
  }

  if (hour >= 17 && hour < 20) {
    return 'dusk'
  }

  if (hour >= 5 && hour < 7) {
    return 'dawn'
  }

  return 'night'
}

/** 环境层滤镜；用户关闭动态表现时返回 none。 */
export function ambientPhaseFilter(motionEnabled: boolean): string {
  if (!motionEnabled) {
    return 'none'
  }

  return PHASE_FILTERS[dayPhaseAt(new Date().getHours())]
}
