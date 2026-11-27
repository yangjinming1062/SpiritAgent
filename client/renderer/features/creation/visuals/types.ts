import type { ApiSchemas } from '../../../../../shared/api.generated'

export type VisualPlayCommand = ApiSchemas['VisualPlayCommand']
export type VisualActualSnapshot = ApiSchemas['ActualPresentationSnapshot']
export type VisualsPresentation = ApiSchemas['VisualPresentationResponse']
export type VisualTargetSurface = VisualPlayCommand['target_surface']

export function commandTargetsSurface(command: VisualPlayCommand, surface: VisualTargetSurface): boolean {
  return (
    command.target_surface === surface &&
    (surface === 'sprite' ? command.asset_kind === 'actor' : command.asset_kind !== 'actor')
  )
}
