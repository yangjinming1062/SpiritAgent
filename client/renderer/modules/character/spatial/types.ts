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

export type Locomotion = 'still' | 'fly' | 'drag'
