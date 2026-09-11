interface SpeechCue {
  before: string
  tag: string
}

export type SpeechStyle =
  | {
      provider: 'mimo'
      model: string
      styles: string[]
      direction: { role: string; scene: string; guidance: string }
      cues: SpeechCue[]
    }
  | {
      provider: 'minimax'
      model: string
      emotion: 'happy' | 'sad' | 'angry' | 'fearful' | 'disgusted' | 'surprised' | 'calm' | 'fluent' | 'whisper' | null
      speed: number
      cues: SpeechCue[]
      pauses: { before: string; seconds: number }[]
    }

export function speechStyleKey(style?: SpeechStyle): string {
  if (!style) {
    return ''
  }

  const cues = style.cues.map(cue => [cue.before, cue.tag])

  return style.provider === 'mimo'
    ? JSON.stringify([
        style.provider,
        style.model,
        style.styles,
        style.direction.role,
        style.direction.scene,
        style.direction.guidance,
        cues
      ])
    : JSON.stringify([
        style.provider,
        style.model,
        style.emotion,
        style.speed,
        cues,
        style.pauses.map(pause => [pause.before, pause.seconds])
      ])
}
