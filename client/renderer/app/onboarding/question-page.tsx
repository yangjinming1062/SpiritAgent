import type { RefObject } from 'react'

import { cn } from '@/shared/lib/utils'
import { Chip, DatePicker, INPUT_CLASS } from '@/shared/panel'

import type { AnswerKind, Question } from './onboarding-questions'

interface QuestionPageProps {
  question: Question
  qIndex: number
  questionCount: number
  lockedLabel?: string
  input: string
  answerKind: AnswerKind | null
  hint: string | null
  inputRef: RefObject<HTMLInputElement | null>
  textareaRef: RefObject<HTMLTextAreaElement | null>
  setInput: (value: string) => void
  setAnswerKind: (kind: AnswerKind | null) => void
  canGoBack: boolean
  onBack: () => void
  onSend: () => void
  onSkip: () => void
}

export function QuestionPage({
  question,
  qIndex,
  questionCount,
  lockedLabel,
  input,
  answerKind,
  hint,
  inputRef,
  textareaRef,
  setInput,
  setAnswerKind,
  canGoBack,
  onBack,
  onSend,
  onSkip
}: QuestionPageProps): React.JSX.Element {
  const presetValues = question.presets ?? []

  return (
    <>
      {lockedLabel && (
        <p className="mb-2 rounded-md border border-amber-300/30 bg-amber-300/10 px-2 py-1 text-[10px] leading-relaxed text-strong">
          「{lockedLabel}」是形象确认后无法再次更改的重点内容，请仔细选择。
        </p>
      )}
      {question && (
        <>
          <p className="min-h-[3.5rem] text-[15px] leading-relaxed">{question.text}</p>
          {presetValues.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {presetValues.map(p => (
                <Chip active={input === p} key={p} label={p} onClick={() => setInput(p)} />
              ))}
            </div>
          )}
          {question.kinds && (
            <div className="mt-3 flex flex-wrap gap-2">
              {question.kinds.map(k => (
                <Chip
                  active={answerKind?.chip === k.chip}
                  key={k.chip}
                  label={k.chip}
                  onClick={() => {
                    setAnswerKind(k)
                    setInput('')
                    inputRef.current?.focus()
                  }}
                />
              ))}
            </div>
          )}
          {answerKind && (
            <>
              <p className="mt-3 text-xs text-body">{answerKind.label}</p>
              {answerKind.values && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {answerKind.values.map(v => (
                    <Chip active={input === v} key={v} label={v} onClick={() => setInput(v)} />
                  ))}
                </div>
              )}
            </>
          )}
          {question.multiline ? (
            <textarea
              className={`mt-3 ${INPUT_CLASS} text-sm`}
              onChange={e => setInput(e.target.value)}
              placeholder={question.placeholder}
              ref={textareaRef}
              rows={3}
              value={input}
            />
          ) : question.date ? (
            <DatePicker
              className="mt-3"
              clearLabel="清除"
              onChange={setInput}
              placeholder={question.placeholder}
              value={input}
            />
          ) : (
            <input
              className={cn(INPUT_CLASS, 'mt-3 text-sm')}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  onSend()
                }
              }}
              placeholder={answerKind?.placeholder ?? question.placeholder}
              ref={inputRef}
              value={input}
            />
          )}
          <div className="mt-4 flex items-center justify-between text-xs">
            <button
              className="text-body transition hover:text-strong disabled:opacity-30"
              disabled={!canGoBack}
              onClick={onBack}
              type="button"
            >
              上一题
            </button>
            <div className="flex gap-3">
              {!question.requiredHint && (
                <button className="text-body transition hover:text-strong" onClick={onSkip} type="button">
                  跳过
                </button>
              )}
              <button
                className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                onClick={onSend}
                type="button"
              >
                {qIndex === questionCount - 1 ? '完成' : '发送'}
              </button>
            </div>
          </div>
          {hint && <p className="mt-2 text-xs text-strong">{hint}</p>}
          <p className="mt-2 text-right text-[10px] text-faint">
            {qIndex + 1} / {questionCount}
          </p>
        </>
      )}
    </>
  )
}
