'use strict'

const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { NtExecutable, NtExecutableResource } = require('resedit')
const { readWindowsExecutableArch } = require('./build-desktop-host.cjs')

const awareness = 'PerMonitorV2, PerMonitor'

function prepareWindowsDpiManifest(executablePath) {
  const exe = NtExecutable.from(fs.readFileSync(executablePath), { ignoreCert: true })
  const resources = NtExecutableResource.from(exe)
  const manifests = resources.entries.filter(entry => entry.type === 24 && entry.id === 1)
  if (!manifests.length) throw new Error('Electron executable has no application manifest')
  let changed = false
  for (const entry of manifests) {
    const xml = Buffer.from(entry.bin).toString('utf8')
    const dpiElement = /(<(?:\w+:)?dpiAwareness\b[^>]*>)[^<]*(<\/(?:\w+:)?dpiAwareness\s*>)/i
    let next
    if (dpiElement.test(xml)) next = xml.replace(dpiElement, `$1${awareness}$2`)
    else {
      const settingsEnd = /<\/(?:\w+:)?windowsSettings\s*>/i
      if (!settingsEnd.test(xml)) throw new Error('Electron manifest has no Windows settings')
      next = xml.replace(
        settingsEnd,
        `<dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">${awareness}</dpiAwareness>$&`
      )
    }
    if (next === xml) continue
    const bytes = Buffer.from(next, 'utf8')
    entry.bin = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
    changed = true
  }
  if (changed) {
    resources.outputResource(exe)
    fs.writeFileSync(executablePath, Buffer.from(exe.generate()))
  }
  return changed
}

function prepareDevelopmentElectron(originalPath) {
  const arch = readWindowsExecutableArch(originalPath)
  const key = crypto
    .createHash('sha256')
    .update(fs.readFileSync(originalPath))
    .update(fs.readFileSync(__filename))
    .update(require('../package.json').devDependencies.resedit)
    .digest('hex')
  const cacheRoot = path.resolve(__dirname, '..', 'build', 'dev-electron', arch)
  const destination = path.join(cacheRoot, key)
  const prepared = path.join(destination, path.basename(originalPath))
  const marker = path.join(destination, '.dpi-prepared')
  if (fs.existsSync(prepared) && fs.existsSync(marker)) return prepared
  const staging = path.join(cacheRoot, `${key}.preparing-${process.pid}`)
  fs.mkdirSync(staging, { recursive: true })
  try {
    fs.cpSync(path.dirname(originalPath), staging, { recursive: true, dereference: true })
    prepareWindowsDpiManifest(path.join(staging, path.basename(originalPath)))
    fs.writeFileSync(path.join(staging, '.dpi-prepared'), awareness)
    fs.mkdirSync(cacheRoot, { recursive: true })
    try {
      fs.renameSync(staging, destination)
    } catch (error) {
      if (!fs.existsSync(prepared) || !fs.existsSync(marker)) throw error
    }
  } finally {
    fs.rmSync(staging, { recursive: true, force: true })
  }
  console.log(`[launch-dev-electron] prepared ${arch} Electron with ${awareness} DPI awareness`)
  return prepared
}

module.exports = { prepareDevelopmentElectron, prepareWindowsDpiManifest }
