// 推理档位的合法值：会话参数与工位默认设置共用，避免两处清单漂移。
export const REASONING_EFFORT_VALUES = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'] as const

export type ReasoningEffort = (typeof REASONING_EFFORT_VALUES)[number]

export function resolveReasoningEffort(value: unknown): ReasoningEffort {
  return REASONING_EFFORT_VALUES.find(effort => effort === value) ?? 'low'
}
