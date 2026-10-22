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

export type SpatialLocale = 'home' | 'perch' | 'roam' | 'target'

export type Locomotion = 'still' | 'walk' | 'fly' | 'drag'

export interface SpatialLocaleOptions {
  position?: Position
  locomotion?: 'walk' | 'fly'
  instant?: boolean
  /** 普通与窗口栖息的缩放上限；缺省时不限。 */
  scaleLimit?: number
}
