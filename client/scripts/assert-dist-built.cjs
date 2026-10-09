'use strict'

const fs = require('node:fs')
const path = require('node:path')

function checkDistBuilt(distDir) {
  distDir = path.resolve(distDir)
  if (!fs.existsSync(distDir) || !fs.statSync(distDir).isDirectory()) {
    return { ok: false, error: `no dist directory at ${distDir}` }
  }

  const requiredHtmlFiles = ['sprite.html', 'living.html', 'workbench.html', 'desktop.html', 'desktop-background.html']
  for (const file of requiredHtmlFiles) {
    const htmlPath = path.join(distDir, file)
    const htmlStat = fs.statSync(htmlPath, { throwIfNoEntry: false })
    if (!htmlStat?.isFile()) {
      return { ok: false, error: `dist/${file} is missing at ${htmlPath}` }
    }
    if (htmlStat.size === 0) {
      return { ok: false, error: `dist/${file} is empty at ${htmlPath}` }
    }

    const html = fs.readFileSync(htmlPath, 'utf8').replace(/<!--[\s\S]*?-->/g, '')
    let hasLocalScript = false
    // Vite 输出使用引号属性；核对各页实际引用，不能由遗留的其他 JS 包替代。
    for (const tag of html.matchAll(/<(script|link)\b([^>]*)>/gi)) {
      const attributes = Object.fromEntries(
        [...tag[2].matchAll(/\s(src|href|rel)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi)].map(match => [
          match[1].toLowerCase(),
          match[2] ?? match[3]
        ])
      )
      const isScript = tag[1].toLowerCase() === 'script'
      if (!isScript && !/(?:^|\s)(?:stylesheet|modulepreload)(?:\s|$)/i.test(attributes.rel ?? '')) continue
      const reference = isScript ? attributes.src : attributes.href
      if (reference === undefined) {
        if (isScript) continue
        return { ok: false, error: `dist/${file} has a resource link without href` }
      }
      if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(reference)) continue
      let resourcePath
      try {
        resourcePath = decodeURIComponent(reference.split(/[?#]/)[0]).replace(/\\/g, '/')
      } catch {
        return { ok: false, error: `dist/${file} has an invalid resource URL: ${reference}` }
      }
      const resource = path.resolve(distDir, resourcePath)
      const relative = path.relative(distDir, resource)
      if (!resourcePath || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative))
        return { ok: false, error: `dist/${file} references a resource outside dist: ${reference}` }
      const stat = fs.statSync(resource, { throwIfNoEntry: false })
      if (!stat?.isFile() || stat.size === 0)
        return { ok: false, error: `dist/${file} references a missing or empty resource: ${reference}` }
      if (isScript) {
        if (!resource.endsWith('.js'))
          return { ok: false, error: `dist/${file} has no built JS script at ${reference}` }
        hasLocalScript = true
      }
    }
    if (!hasLocalScript) return { ok: false, error: `dist/${file} has no local built JS script` }
  }

  return { ok: true }
}

function main() {
  const desktopRoot = path.resolve(__dirname, '..')
  const distDir = path.join(desktopRoot, 'dist')
  const result = checkDistBuilt(distDir)

  if (!result.ok) {
    console.error(`\n✗ assert-dist-built: ${result.error}`)
    console.error('  The renderer bundle is missing or incomplete, so packaging')
    console.error('  would produce an app that launches to a blank page.')
    console.error('  Re-run the build and check the tsc/vite output above for the')
    console.error('  real failure, then package again:')
    console.error(`    cd ${desktopRoot} && pnpm run build\n`)
    process.exit(1)
  }

  console.log(
    '✓ assert-dist-built: six HTML entries and their local JS, stylesheet and modulepreload resources are complete'
  )
}

module.exports = { checkDistBuilt }

if (require.main === module) main()
