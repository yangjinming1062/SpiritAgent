import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { AlertCircle, ChevronDown, FileText, Loader2, Volume2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import styles from './chat-voice-bar.module.css'
import {
  $voiceBarFailedIds,
  $voiceBarLoadingId,
  $voiceBarPausedId,
  $voiceBarPlayingId,
  voiceBarControl
} from './voice-link'
import { $voicePlaybackRecords } from './voice-playback'

// 时长来自后端音频，播放状态经 voice-link 投影；本组件不发起文字合成。

interface ChatVoiceBarProps {
  duration?: number
  messageId: string
  playbackKey?: string
}

export function ChatVoiceBar({ duration, messageId, playbackKey }: ChatVoiceBarProps): React.JSX.Element {
  const dict = useStrings()
  const t = dict.chat.voice
  const playingId = useStore($voiceBarPlayingId)
  const loadingId = useStore($voiceBarLoadingId)
  const pausedId = useStore($voiceBarPausedId)
  const failedIds = useStore($voiceBarFailedIds, { keys: [messageId] })
  const records = useStore($voicePlaybackRecords, { keys: playbackKey ? [playbackKey] : [] })
  const record = playbackKey ? records[playbackKey] : undefined
  const unavailable = failedIds[messageId]

  const isPlaying = playingId === messageId
  const isLoading = loadingId === messageId
  const position = record?.positionSeconds ?? 0
  const listened = record?.listened === true
  const isPaused = !isPlaying && !isLoading && (pausedId === messageId || position > 0)

  const hasRealDuration = typeof duration === 'number' && duration > 0
  const effectiveSec = hasRealDuration ? duration : 1
  const sec = Math.max(1, Math.min(60, effectiveSec))
  const widthPx = 88 + Math.round(((sec - 1) / 59) * (220 - 88))

  const state = isLoading
    ? 'loading'
    : isPlaying
      ? 'playing'
      : unavailable
        ? 'failed'
        : isPaused
          ? 'paused'
          : listened
            ? 'listened'
            : 'unheard'

  const action = isPlaying || isLoading ? t.pause : unavailable ? t.retry : isPaused ? t.resume : t.play
  const label = [t[state], hasRealDuration ? `${Math.ceil(duration)}″` : '', action].filter(Boolean).join(', ')
  const progress = hasRealDuration ? Math.min(100, (position / duration) * 100) : 0

  const handleClick = (e: React.MouseEvent): void => {
    e.stopPropagation()
    voiceBarControl().toggle(messageId)
  }

  return (
    <span className="relative inline-flex max-w-full pr-3 align-top">
      <button
        aria-label={label}
        aria-pressed={isPlaying}
        className={cn(
          'group/voicebar relative inline-flex h-9 max-w-full items-center justify-between overflow-hidden rounded-lg px-3.5 text-xs backdrop-blur-md transition select-none cursor-pointer',
          'border border-line-standard bg-surface-card text-strong shadow-xs hover:border-line-strong hover:bg-surface-card/90',
          isPlaying && 'bg-accent-soft/40 border-accent-line/50',
          unavailable && !isLoading && 'text-danger-fg'
        )}
        data-voice-state={state}
        onClick={handleClick}
        style={{ width: `${widthPx}px` }}
        title={label}
        type="button"
      >
        <div className="flex items-center gap-1.5">
          {isLoading ? (
            <Loader2 className="size-3.5 shrink-0 text-accent animate-spin" />
          ) : isPlaying ? (
            <Volume2 className={cn('size-4 shrink-0 text-accent', styles.playing)} />
          ) : unavailable ? (
            <AlertCircle className="size-4 shrink-0 text-danger-fg" />
          ) : (
            <Volume2 className="size-3.5 shrink-0 text-muted group-hover/voicebar:text-strong transition-colors" />
          )}
        </div>
        <span
          className={cn(
            'ml-2 text-[11px] font-medium tabular-nums',
            isPlaying ? 'text-accent font-semibold' : 'text-muted'
          )}
        >
          {unavailable && !isLoading ? t.retryShort : hasRealDuration ? `${Math.ceil(duration)}″` : '…'}
        </span>
        {(position > 0 || isPlaying) && hasRealDuration ? (
          <span
            aria-label={t.progress}
            aria-valuemax={100}
            aria-valuemin={0}
            aria-valuenow={Math.round(progress)}
            className="absolute inset-x-3 bottom-1 h-0.5 overflow-hidden rounded-full bg-fill-hover"
            role="progressbar"
          >
            <span className="block h-full rounded-full bg-accent" style={{ width: `${progress}%` }} />
          </span>
        ) : null}
      </button>
      {!listened ? (
        <span aria-hidden="true" className="absolute right-0 top-1 size-1.5 rounded-full bg-red-500" />
      ) : null}
    </span>
  )
}

interface TranscriptBlockProps {
  text: string
}

export function TranscriptBlock({ text }: TranscriptBlockProps): React.JSX.Element | null {
  const dict = useStrings()
  const [expanded, setExpanded] = useState(false)
  const trimmed = text.trim()

  if (!trimmed) {
    return null
  }

  return (
    <div className="mt-1 flex max-w-full flex-col select-none">
      <button
        aria-expanded={expanded}
        className={cn(
          'group/transcript inline-flex items-center gap-1 rounded-md border border-line-standard bg-surface-card px-2 py-0.5 text-[10px] text-muted backdrop-blur-xs transition-all duration-150',
          'hover:border-line-strong hover:bg-surface-card/90 hover:text-strong cursor-pointer text-left shadow-xs'
        )}
        onClick={() => setExpanded(prev => !prev)}
        type="button"
      >
        <FileText className="size-2.5 shrink-0 text-muted group-hover/transcript:text-strong transition-colors" />
        <span className="font-normal text-muted group-hover/transcript:text-strong transition-colors">
          {expanded ? dict.chat.voice.collapse : dict.chat.voice.showTranscript}
        </span>
        <ChevronDown
          className={cn(
            'size-2.5 shrink-0 text-muted transition-transform duration-150 group-hover/transcript:text-strong',
            expanded ? 'rotate-180' : '-rotate-90'
          )}
        />
      </button>
      {expanded ? (
        <div className="mt-1 max-h-60 max-w-full overflow-y-auto rounded-lg border border-line-standard bg-surface-card p-2.5 text-xs leading-relaxed text-strong shadow-inner backdrop-blur-md select-text cursor-text whitespace-pre-wrap break-words font-sans">
          {trimmed}
        </div>
      ) : null}
    </div>
  )
}
