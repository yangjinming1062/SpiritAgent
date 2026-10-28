import type { ActionDefinitionRequest } from './protocol'

export function parseActionConditions(value: string): string[] {
  return value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
}

// 与后端纯语义词表由 generate_protocol.py 核对；两端表单共用。
export const ACTION_SEMANTIC_TAGS = [
  'idle',
  'attention',
  'interaction',
  'reading',
  'working',
  'creating',
  'thinking',
  'resting',
  'organizing',
  'socializing',
  'stretching',
  'grooming',
  'drinking',
  'dancing'
] as const

export type ActionSemanticTag = (typeof ACTION_SEMANTIC_TAGS)[number]

export const ACTION_PERFORMANCE_ROLES = ['idle', 'sustained', 'reaction'] as const
export const ACTION_MOTION_INTENSITIES = ['subtle', 'normal', 'expressive'] as const

/** 两端表单与动作定义契约一致，字符串长度按 Unicode 码点计算。 */
export function isActionDesignValid(design: ActionDefinitionRequest): boolean {
  const length = (value: string): number => Array.from(value.trim()).length
  const nameLength = length(design.name)
  const motionLength = length(design.motion_description)
  const duration = design.duration_seconds ?? 4
  const triggers = design.allowed_triggers ?? []
  const semanticTags = design.semantic_tags ?? []

  return (
    nameLength > 0 &&
    nameLength <= 64 &&
    motionLength >= 10 &&
    motionLength <= 600 &&
    Number.isInteger(duration) &&
    duration >= 1 &&
    duration <= 15 &&
    ['once', 'loop'].includes(design.clip_kind ?? 'once') &&
    triggers.length <= 8 &&
    triggers.every(trigger => ['chat', 'manual', 'periodic', 'user_return'].includes(trigger)) &&
    semanticTags.length <= 8 &&
    semanticTags.every(tag => (ACTION_SEMANTIC_TAGS as readonly string[]).includes(tag)) &&
    (design.performance_role === null ||
      design.performance_role === undefined ||
      (ACTION_PERFORMANCE_ROLES as readonly string[]).includes(design.performance_role)) &&
    (!design.performance_role ||
      (design.performance_role === 'reaction') === ((design.clip_kind ?? 'once') === 'once')) &&
    (design.interruptible === undefined || typeof design.interruptible === 'boolean') &&
    (ACTION_MOTION_INTENSITIES as readonly string[]).includes(design.motion_intensity ?? 'normal') &&
    length(design.reason ?? '') <= 400 &&
    [design.use_when ?? [], design.avoid_when ?? []].every(
      conditions => conditions.length <= 8 && conditions.every(condition => length(condition) <= 120)
    )
  )
}
