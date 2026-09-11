import { CHIP_FILTER, CHIP_FILTER_ACTIVE } from './palette'

export interface ChipProps {
  active?: boolean
  label: string
  onClick: () => void
}

// 设置页反复出现的"过滤/选项"小圆角按钮——
// active 时切换到 CHIP_FILTER_ACTIVE 着色，否则保持 CHIP_FILTER。
// 取代 persona-editor / voice-page / memory-section 中手写 <button>。
export function Chip({ active, label, onClick }: ChipProps): React.JSX.Element {
  return (
    <button className={active ? CHIP_FILTER_ACTIVE : CHIP_FILTER} onClick={onClick} type="button">
      {label}
    </button>
  )
}
