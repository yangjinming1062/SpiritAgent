import type { PresentationState } from '@ipc/contracts'

export function presentationDisplayOptions(
  state: Pick<PresentationState, 'displays' | 'displayId'>,
  labels: { chooseDisplay: string; primaryDisplaySuffix: string }
): Array<{ value: string; label: string }> {
  const options = state.displays.map(display => ({
    value: String(display.id),
    label: `${display.label}${display.isPrimary ? labels.primaryDisplaySuffix : ''} · ${display.width} × ${display.height}`
  }))

  return state.displayId === null ? [{ value: '', label: labels.chooseDisplay }, ...options] : options
}
