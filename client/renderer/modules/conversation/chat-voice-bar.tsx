import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { ChevronDown, FileText, Loader2, Volume2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import { $voiceBarFailedId, $voiceBarLoadingId, $voiceBarPlayingId, voiceBarControl } from './voice-link'

// 时长来自后端音频，播放状态经 voice-link 投影；本组件不发起文字合成。

interface ChatVoiceBarProps {
  duration?: number
  messageId: string
  failed?: boolean
}

export function ChatVoiceBar({ duration, messageId, failed }: ChatVoiceBarProps): React.JSX.Element {
  const dict = useStrings()
  const playingId = useStore($voiceBarPlayingId)
  const loadingId = useStore($voiceBarLoadingId)
  const failedId = useStore($voiceBarFailedId)
  const unavailable = failed || failedId === messageId

  const isPlaying = playingId === messageId
  const isLoading = loadingId === messageId

  const hasRealDuration = typeof duration === 'number' && duration > 0
  const effectiveSec = hasRealDuration ? duration : 1
  const sec = Math.max(1, Math.min(60, effectiveSec))
  const widthPx = 76 + Math.round(((sec - 1) / 59) * (220 - 76))

  const handleClick = (e: React.MouseEvent): void => {
    e.stopPropagation()
    voiceBarControl().toggle(messageId)
  }

  return (
    <button
      aria-label={
        isPlaying || isLoading ? dict.chat.voice.stop : unavailable ? dict.chat.voice.retry : dict.chat.voice.play
      }
      className={cn(
        'group/voicebar relative inline-flex items-center justify-between rounded-2xl px-3.5 py-2 text-xs backdrop-blur-md transition select-none cursor-pointer',
        'border border-line-standard bg-surface-card text-strong shadow-xs hover:border-line-strong hover:bg-surface-card/90',
        isPlaying && 'bg-accent-soft/40 border-accent-line/50'
      )}
      onClick={handleClick}
      style={unavailable ? undefined : { width: `${widthPx}px` }}
      type="button"
    >
      <div className="flex items-center gap-1.5">
        {isLoading ? (
          <Loader2 className="size-3.5 shrink-0 text-accent animate-spin" />
        ) : isPlaying ? (
          <Volume2 className="size-3.5 shrink-0 text-accent animate-pulse" />
        ) : (
          <Volume2 className="size-3.5 shrink-0 text-muted group-hover/voicebar:text-strong transition-colors" />
        )}
      </div>
      <span
        className={cn(
          'ml-2 text-[11px] font-medium tracking-tight',
          isPlaying ? 'text-accent font-semibold' : 'text-muted'
        )}
      >
        {unavailable ? dict.chat.voice.retry : hasRealDuration ? `${Math.ceil(duration)}″` : '…'}
      </span>
    </button>
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
