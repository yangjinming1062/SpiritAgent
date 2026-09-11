import { Component, type ErrorInfo, type ReactNode } from 'react'

import { log } from '@/shared/lib/log'
import { BTN_PRIMARY, BTN_SUBTLE, EmptyState } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface ErrorBoundaryFallbackProps {
  error: Error
  reset: () => void
}

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: (props: ErrorBoundaryFallbackProps) => ReactNode
  label?: string
  onError?: (error: Error, info: ErrorInfo) => void
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    log.error(this.props.label ? `error-boundary:${this.props.label}` : 'error-boundary', error, info.componentStack)
    this.props.onError?.(error, info)
  }

  reset = () => {
    this.setState({ error: null })
  }

  override render() {
    const { error } = this.state

    if (!error) {
      return this.props.children
    }

    if (this.props.fallback) {
      return this.props.fallback({ error, reset: this.reset })
    }

    return <RootErrorFallback error={error} reset={this.reset} />
  }
}

function RootErrorFallback({ error, reset }: ErrorBoundaryFallbackProps): React.JSX.Element {
  const t = useStrings()

  return (
    <div className="fixed inset-0 z-[1500] grid place-items-center bg-surface-chrome p-6 text-strong">
      <div className="w-full max-w-md">
        <EmptyState
          action={
            <div className="flex gap-2">
              <button className={BTN_PRIMARY} onClick={reset} type="button">
                {t.common.retry}
              </button>
              <button className={BTN_SUBTLE} onClick={() => window.location.reload()} type="button">
                {t.errors.reloadWindow}
              </button>
            </div>
          }
          description={error.message || t.errors.boundaryDesc}
          title={t.errors.boundaryTitle}
        />
      </div>
    </div>
  )
}
