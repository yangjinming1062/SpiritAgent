import path from 'node:path'

export function venvPythonFor(spiritagentHome: string, platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32'
    ? path.join(spiritagentHome, 'runner', '.venv', 'Scripts', 'python.exe')
    : path.join(spiritagentHome, 'runner', '.venv', 'bin', 'python')
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

  if (fileExists(venvPython)) {
    return { args: ['-I', '-X', 'utf8', '-m', 'spirit_agent_runner'], command: venvPython, kind: 'venv-python' }
  }

  return null
}
