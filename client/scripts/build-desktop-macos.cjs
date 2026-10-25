'use strict'

const { createHash } = require('node:crypto')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const clientRoot = path.resolve(__dirname, '..')
const cpuTypes = { 0x01000007: 'x64', 0x0100000c: 'arm64' }

function macosArchFromBuilder(arch) {
  const name = { 1: 'x64', 3: 'arm64' }[arch]
  if (!name) throw new Error(`Unsupported macOS desktop architecture: ${arch}`)
  return name
}

function readMacosArchitectures(filename) {
  const file = fs.openSync(filename, 'r')
  const header = Buffer.alloc(4096)
  let length
  try {
    length = fs.readSync(file, header, 0, header.length, 0)
  } finally {
    fs.closeSync(file)
  }
  if (length < 8) throw new Error(`Invalid Mach-O image: ${filename}`)
  const magic = header.readUInt32BE(0)
  if (magic === 0xcffaedfe) {
    const arch = cpuTypes[header.readUInt32LE(4)]
    if (arch) return [arch]
  } else if (magic === 0xcafebabe || magic === 0xcafebabf) {
    const count = header.readUInt32BE(4)
    const stride = magic === 0xcafebabf ? 32 : 20
    if (count > 0 && 8 + count * stride <= length) {
      const architectures = Array.from(
        { length: count },
        (_, index) => cpuTypes[header.readUInt32BE(8 + index * stride)]
      )
      if (architectures.every(Boolean)) return architectures
    }
  }
  throw new Error(`Unsupported Mach-O image: ${filename}`)
}

function readMacosExecutableArch(filename) {
  const architectures = readMacosArchitectures(filename)
  if (architectures.length === 1) return architectures[0]
  // 通用开发二进制沿用启动 Node 的架构，兼容 Rosetta 开发环境。
  if (architectures.includes(process.arch)) return process.arch
  throw new Error('Cannot determine the development Electron architecture')
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', timeout: 180_000 })
  if (result.error || result.status !== 0) {
    throw new Error(`${command} failed: ${result.error?.message || `exit ${result.status ?? 'unknown'}`}`)
  }
}

function hasNodeHeaders(directory) {
  return ['node_api.h', 'node_api_types.h', 'js_native_api.h', 'js_native_api_types.h'].every(file =>
    fs.existsSync(path.join(directory, file))
  )
}

function nodeHeaders() {
  const version = process.versions.node
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error(`Unsupported Node header version: ${version}`)
  const cached = path.join(clientRoot, 'build', 'node-headers', version)
  const candidates = [
    process.env.npm_config_nodedir && path.join(process.env.npm_config_nodedir, 'include', 'node'),
    path.resolve(path.dirname(process.execPath), '..', 'include', 'node'),
    path.join(os.homedir(), 'Library', 'Caches', 'node-gyp', version, 'include', 'node'),
    path.join(os.homedir(), '.cache', 'node-gyp', version, 'include', 'node'),
    path.join(cached, 'include', 'node')
  ].filter(Boolean)
  const found = candidates.find(hasNodeHeaders)
  if (found) return found

  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-node-headers-'))
  try {
    const archive = path.join(temporary, 'headers.tar.gz')
    console.log(`[desktop-macos] downloading Node ${version} C Node-API headers`)
    run('curl', [
      '--fail',
      '--location',
      '--max-time',
      '120',
      '--output',
      archive,
      `https://nodejs.org/dist/v${version}/node-v${version}-headers.tar.gz`
    ])
    const extracted = path.join(temporary, 'headers')
    fs.mkdirSync(extracted)
    run('tar', ['-xzf', archive, '-C', extracted, '--strip-components=1', `node-v${version}/include/node`])
    if (!hasNodeHeaders(path.join(extracted, 'include', 'node')))
      throw new Error('Downloaded Node headers are incomplete')
    fs.mkdirSync(path.dirname(cached), { recursive: true })
    // 完整解压后再发布缓存，并发的另一架构构建只能看到完整头文件。
    try {
      fs.renameSync(extracted, cached)
    } catch (error) {
      if (!hasNodeHeaders(path.join(cached, 'include', 'node'))) throw error
    }
    return path.join(cached, 'include', 'node')
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true })
  }
}

function buildDesktopMacos(arch = process.arch, { ifNeeded = false } = {}) {
  if (process.platform !== 'darwin') throw new Error('macOS desktop components must be built on macOS')
  if (!['x64', 'arm64'].includes(arch)) throw new Error(`Unsupported macOS desktop architecture: ${arch}`)
  const source = path.join(clientRoot, 'desktop-macos', 'desktop-macos.mm')
  const output = path.join(clientRoot, 'build', arch, 'desktop_macos.node')
  const manifest = `${output}.build.json`
  const deploymentTarget = process.env.MACOSX_DEPLOYMENT_TARGET || '12.0'
  const fingerprint = createHash('sha256')
    .update(fs.readFileSync(source))
    .update(fs.readFileSync(__filename))
    .update(JSON.stringify({ arch, deploymentTarget, node: process.versions.node }))
    .digest('hex')
  if (ifNeeded && fs.existsSync(output) && fs.existsSync(manifest)) {
    try {
      if (
        JSON.parse(fs.readFileSync(manifest, 'utf8')).fingerprint === fingerprint &&
        readMacosArchitectures(output).join() === arch
      ) {
        console.log(`[desktop-macos] reusing ${arch} development component: ${output}`)
        return
      }
    } catch (error) {
      console.warn(`[desktop-macos] development component must be rebuilt: ${error.message}`)
    }
  }
  const include = nodeHeaders()
  fs.mkdirSync(path.dirname(output), { recursive: true })
  const temporary = `${output}.${process.pid}.tmp`
  try {
    run('xcrun', [
      'clang++',
      '-std=c++17',
      '-fobjc-arc',
      '-fmodules',
      '-bundle',
      '-undefined',
      'dynamic_lookup',
      '-DNAPI_VERSION=8',
      '-I',
      include,
      '-arch',
      arch === 'x64' ? 'x86_64' : 'arm64',
      `-mmacosx-version-min=${deploymentTarget}`,
      '-O2',
      '-framework',
      'AppKit',
      '-framework',
      'ApplicationServices',
      '-framework',
      'CoreGraphics',
      '-framework',
      'Foundation',
      source,
      '-o',
      temporary
    ])
    if (readMacosArchitectures(temporary).join() !== arch)
      throw new Error(`macOS desktop component architecture does not match ${arch}`)
    fs.renameSync(temporary, output)
    fs.writeFileSync(manifest, JSON.stringify({ fingerprint }))
  } finally {
    fs.rmSync(temporary, { force: true })
  }
  console.log(`[desktop-macos] built ${arch}: ${output}`)
}

module.exports = { buildDesktopMacos, macosArchFromBuilder, readMacosArchitectures, readMacosExecutableArch }

if (require.main === module) {
  try {
    const args = process.argv.slice(2)
    if (args.length && (args.length !== 2 || args[0] !== '--arch' || !['x64', 'arm64'].includes(args[1]))) {
      throw new Error('Usage: build-desktop-macos.cjs [--arch x64|arm64]')
    }
    buildDesktopMacos(args[1])
  } catch (error) {
    console.error(`[desktop-macos] ${error.message}`)
    process.exitCode = 1
  }
}
