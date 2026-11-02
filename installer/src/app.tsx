import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'
import { $route, $bootstrap, $progress, initialize } from './store'
import { Ambience, type AmbienceTone } from './components/ambience'
import { Titlebar } from './components/titlebar'
import Welcome from './routes/welcome'
import Progress from './routes/progress'
import Success from './routes/success'
import Failure from './routes/failure'

export default function App(): React.JSX.Element {
  const route = useStore($route)
  const bootstrap = useStore($bootstrap)
  const progress = useStore($progress)

  useEffect(() => {
    void initialize()
  }, [])

  const tone: AmbienceTone =
    route === 'failure'
      ? 'dim'
      : route === 'success'
        ? 'bloom'
        : route === 'progress' && bootstrap.status === 'completed'
          ? 'bloom'
          : route === 'progress' && bootstrap.status === 'running'
            ? 'active'
            : 'calm'

  const rise =
    route === 'progress'
      ? progress.total
        ? progress.done / progress.total
        : 0
      : route === 'success'
        ? 0.38
        : 0

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <Ambience tone={tone} rise={rise} />
      <Titlebar />
      <main className="relative z-10 flex flex-1 flex-col overflow-hidden">
        {route === 'welcome' && <Welcome />}
        {route === 'progress' && <Progress bootstrap={bootstrap} />}
        {route === 'success' && <Success />}
        {route === 'failure' && <Failure bootstrap={bootstrap} />}
      </main>
    </div>
  )
}
