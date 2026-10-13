import type { WritableAtom } from 'nanostores'

import type { SpatialPeek } from '../spatial-peek'

export interface Position {
  x: number
  y: number
}

export interface ViewportSize {
  width: number
  height: number
}

export interface SpriteRect {
  left: number
  top: number
  right: number
  bottom: number
}

export interface ScreenEdge {
  side: 'left' | 'right'
  yRatio: number
}

export type SpatialLocale = 'home' | 'perch' | 'roam' | 'screen_peek' | 'window_peek' | 'target' | 'workbench'

export type Locomotion = 'still' | 'walk' | 'fly' | 'drag'

export type WindowPeekBinding = Pick<
  Extract<SpatialPeek, { mode: 'window' }>,
  'runnerInstanceId' | 'windowId' | 'windowPid'
>

export type PeekPreparation = {
  action: 'peek_left' | 'peek_right'
  generation: number
  packId: number
} & ({ mode: 'screen'; animate: boolean; screenEdge: ScreenEdge } | ({ mode: 'window' } & WindowPeekBinding))

export interface WindowPeekIntent extends WindowPeekBinding {
  packId: number
  generation: number
}

export interface SpatialLocaleOptions {
  position?: Position
  locomotion?: 'walk' | 'fly'
  instant?: boolean
  /** 普通与窗口栖息的缩放上限；缺省时不限。 */
  scaleLimit?: number
}

export interface SpatialState {
  $defaultScale: WritableAtom<number>
  $homePosition: WritableAtom<Position>
  $peekPreparation: WritableAtom<PeekPreparation | null>
  $spatialLocale: WritableAtom<SpatialLocale>
  $spatialLocomotion: WritableAtom<Locomotion>
  $spatialPeek: WritableAtom<SpatialPeek | null>
  $spatialPos: WritableAtom<Position>
  $spatialScale: WritableAtom<number>
  $spriteCanvasRect: WritableAtom<SpriteRect | null>
}
