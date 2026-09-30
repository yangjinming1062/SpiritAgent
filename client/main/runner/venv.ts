import path from 'node:path'

export function venvPythonFor(spiritagentHome: string, platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32'
    ? path.join(spiritagentHome, 'runner', '.venv', 'Scripts', 'python.exe')
    : path.join(spiritagentHome, 'runner', '.venv', 'bin', 'python')
}

// 更新器的安装目标与 Runner 的启动入口共用这一路径。
export function runnerServerPyFor(spiritagentHome: string): string {
  return path.join(spiritagentHome, 'runner', 'server.py')
}

interface ResolveVenvPythonOptions {
  spiritagentHome?: null | string
  fileExists?: (p: string) => boolean
  platform?: NodeJS.Platform
}

export function resolveVenvPython(
  opts: ResolveVenvPythonOptions = {}
): null | { args: string[]; command: string; kind: string } {
  const { spiritagentHome, fileExists, platform } = opts

  if (!spiritagentHome || typeof fileExists !== 'function') {
    return null
  }

  const venvPython = venvPythonFor(spiritagentHome, platform)
  const serverPy = runnerServerPyFor(spiritagentHome)

  if (fileExists(venvPython) && fileExists(serverPy)) {
    return { args: [serverPy], command: venvPython, kind: 'venv-python' }
  }

  return null
}
