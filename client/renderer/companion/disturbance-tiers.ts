import type { DisturbanceTier } from './companion-store'

interface DisturbanceTierOption {
  hint: string
  id: DisturbanceTier
  label: string
}

export const DISTURBANCE_TIERS: readonly DisturbanceTierOption[] = [
  { id: 'still', label: '静止', hint: '不发起任何主动行为，只回应你' },
  { id: 'normal', label: '常规', hint: '文字问候等原地轻互动' },
  { id: 'autonomous', label: '自主', hint: '自由移动与语音，全能力开放' }
] as const
