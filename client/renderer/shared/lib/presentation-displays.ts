import type { PresentationState } from '@ipc/contracts'

export function presentationDisplayOptions(
  state: Pick<PresentationState, 'displays' | 'displayId'>,
  english: boolean
): Array<{ value: string; label: string }> {
  const options = state.displays.map(display => ({
    value: String(display.id),
    label: `${display.label}${display.isPrimary ? (english ? ' (Primary)' : '（主屏）') : ''} · ${display.width} × ${display.height}`
  }))

  return state.displayId === null
    ? [{ value: '', label: english ? 'Choose a display' : '请选择显示屏' }, ...options]
    : options
}
