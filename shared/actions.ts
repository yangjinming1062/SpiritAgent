import type { ActionDefinitionRequest } from './protocol'

export function parseActionConditions(value: string): string[] {
  return value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
}

/** 两端表单与动作定义契约一致，字符串长度按 Unicode 码点计算。 */
export function isActionDesignValid(design: ActionDefinitionRequest): boolean {
  const length = (value: string): number => Array.from(value.trim()).length
  const nameLength = length(design.name)
  const motionLength = length(design.motion_description)
  const duration = design.duration_seconds ?? 4
  const triggers = design.allowed_triggers ?? []

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
    length(design.reason ?? '') <= 400 &&
    [design.use_when ?? [], design.avoid_when ?? []].every(
      conditions => conditions.length <= 8 && conditions.every(condition => length(condition) <= 120)
    )
  )
}
