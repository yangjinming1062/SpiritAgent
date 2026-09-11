import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'
import { $route, $bootstrap, initialize } from './store'
import { Titlebar } from './components/titlebar'
import Welcome from './routes/welcome'
import Progress from './routes/progress'
import Success from './routes/success'
import Failure from './routes/failure'

export default function App(): React.JSX.Element {
  const route = useStore($route)
  const bootstrap = useStore($bootstrap)

  useEffect(() => {
    void initialize()
  }, [])

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
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
