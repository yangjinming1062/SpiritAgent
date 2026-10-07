const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { execFile } = require('node:child_process')

function run(command, args, { timeout = 10 * 60_000, secrets = [] } = {}) {
  return new Promise((resolve, reject) => {
    const env = { ...process.env }
    delete env.APPLE_API_KEY
    delete env.APPLE_APP_SPECIFIC_PASSWORD
    execFile(command, args, { timeout, env }, (error, stdout, stderr) => {
      if (error) {
        const detail = error.killed
          ? `timed out after ${timeout / 60_000} minutes`
          : stderr?.trim() || stdout?.trim() || error.code || error.message
        const safeDetail = secrets.reduce((message, secret) => message.split(secret).join('[redacted]'), String(detail))
        reject(new Error(`${command} failed: ${safeDetail}`))
        return
      }
      resolve()
    })
  })
}

function resolveApiKeyPath(value) {
  if (fs.existsSync(value)) {
    return { keyPath: value }
  }

  if (!value.includes('BEGIN PRIVATE KEY') || !value.includes('END PRIVATE KEY')) {
    throw new Error('APPLE_API_KEY must be a file path or inline .p8 key content')
  }

  const tempPath = path.join(os.tmpdir(), `spiritagent-notary-${Date.now()}-${process.pid}.p8`)
  fs.writeFileSync(tempPath, value, { encoding: 'utf8', mode: 0o600 })
  return {
    keyPath: tempPath,
    cleanup: () => {
      try {
        fs.rmSync(tempPath, { force: true })
      } catch (err) {
        console.warn(`[notarize] could not remove temporary API key ${tempPath}: ${err.message}`)
      }
    }
  }
}

function resolveCredentials() {
  const profile = String(process.env.APPLE_NOTARY_PROFILE || '').trim()
  const appleId = String(process.env.APPLE_ID || '').trim()
  const password = String(process.env.APPLE_APP_SPECIFIC_PASSWORD || '').trim()
  const teamId = String(process.env.APPLE_TEAM_ID || '').trim()
  if (!profile && (appleId || password)) {
    if (!appleId || !password || !teamId)
      throw new Error('APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, and APPLE_TEAM_ID must all be configured')
    return { args: ['--apple-id', appleId, '--password', password, '--team-id', teamId], secrets: [password] }
  }

  const rawApiKey = String(process.env.APPLE_API_KEY || '').trim()
  const keyId = String(process.env.APPLE_API_KEY_ID || '').trim()
  const issuer = String(process.env.APPLE_API_ISSUER || '').trim()
  if (!profile && (rawApiKey || keyId || issuer)) {
    if (!rawApiKey || !keyId || !issuer)
      throw new Error('APPLE_API_KEY, APPLE_API_KEY_ID, and APPLE_API_ISSUER must all be configured')
    const { keyPath, cleanup } = resolveApiKeyPath(rawApiKey)
    return { args: ['--key', keyPath, '--key-id', keyId, '--issuer', issuer], secrets: [rawApiKey], cleanup }
  }

  const keychainProfile = profile || String(process.env.APPLE_KEYCHAIN_PROFILE || '').trim()
  if (!keychainProfile) return null
  const keychain = String(process.env.APPLE_KEYCHAIN || '').trim()
  const args = ['--keychain-profile', keychainProfile]
  if (keychain) args.push('--keychain', keychain)
  return { args, secrets: [] }
}

exports.default = async function notarize(context) {
  const { electronPlatformName, appOutDir, packager } = context
  if (electronPlatformName !== 'darwin') return

  const appName = packager.appInfo.productFilename
  const appPath = path.join(appOutDir, `${appName}.app`)
  if (!fs.existsSync(appPath)) {
    throw new Error(`Cannot notarize missing app bundle: ${appPath}`)
  }

  const credentials = resolveCredentials()
  if (!credentials) {
    console.log('Skipping notarization: no Apple notarization credentials are configured.')
    return
  }

  const zipPath = path.join(appOutDir, `${appName}.zip`)
  const runOptions = { secrets: credentials.secrets }
  try {
    await run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appPath, zipPath], runOptions)
    await run('xcrun', ['notarytool', 'submit', zipPath, ...credentials.args, '--wait'], {
      ...runOptions,
      timeout: 60 * 60_000
    })
    await run('xcrun', ['stapler', 'staple', '-v', appPath], runOptions)
  } finally {
    try {
      fs.rmSync(zipPath, { force: true })
    } catch (error) {
      console.warn(`[notarize] could not remove temporary archive ${zipPath}: ${error.message}`)
    }
    credentials.cleanup?.()
  }
}
