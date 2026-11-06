import fs from 'node:fs'
import path from 'node:path'

import ts from 'typescript'

// 创作域内的独立子域：造型、环境、影像与动作各有自己的状态与任务，互相只经入口协作。
const CREATION_SUBDOMAINS = new Set(['wardrobe', 'environment', 'visuals', 'actions'])

// 只有业务功能可以发起后端请求；公共 UI 库必须由调用方给出数据与回调。
const BUSINESS_REQUEST_MODULES = new Set(['shared/lib/resource-request'])
const BUSINESS_REQUEST_HELPERS = new Set(['authedResource', 'backendApi', 'requestResource'])

const canonical = filename => {
  const resolved = path.resolve(filename).replaceAll('\\', '/')

  return ts.sys.useCaseSensitiveFileNames ? resolved : resolved.toLowerCase()
}

function directories(directory) {
  return new Set(
    fs
      .readdirSync(directory, { withFileTypes: true })
      .filter(entry => entry.isDirectory())
      .map(entry => (ts.sys.useCaseSensitiveFileNames ? entry.name : entry.name.toLowerCase()))
  )
}

function readProject(clientRoot, filename) {
  const configPath = path.join(clientRoot, filename)
  const config = ts.readConfigFile(configPath, ts.sys.readFile)

  if (config.error) {
    throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'))
  }

  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, clientRoot)

  if (parsed.errors.length) {
    throw new Error(parsed.errors.map(error => ts.flattenDiagnosticMessageText(error.messageText, '\n')).join('\n'))
  }

  return parsed
}

function literalString(node) {
  if (node?.type === 'Literal' && typeof node.value === 'string') {
    return node.value
  }

  if (node?.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis[0].value.cooked
  }

  if (node?.type === 'BinaryExpression' && node.operator === '+') {
    const left = literalString(node.left)
    const right = literalString(node.right)

    return left !== undefined && right !== undefined ? left + right : undefined
  }

  return undefined
}

function propertyName(node) {
  return node.computed ? literalString(node.property) : node.property.name
}

function staticModule(statement) {
  if (ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement)) {
    return statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier)
      ? statement.moduleSpecifier
      : undefined
  }

  if (ts.isImportEqualsDeclaration(statement) && ts.isExternalModuleReference(statement.moduleReference)) {
    const expression = statement.moduleReference.expression

    return expression && ts.isStringLiteral(expression) ? expression : undefined
  }

  return undefined
}

function isValueDependency(statement) {
  if (ts.isImportDeclaration(statement)) {
    const clause = statement.importClause

    if (!clause) {
      return true
    }

    if (clause.isTypeOnly) {
      return false
    }

    return Boolean(
      clause.name ||
      (clause.namedBindings &&
        (ts.isNamespaceImport(clause.namedBindings) ||
          clause.namedBindings.elements.some(element => !element.isTypeOnly)))
    )
  }

  if (ts.isExportDeclaration(statement)) {
    return (
      !statement.isTypeOnly &&
      (!statement.exportClause ||
        ts.isNamespaceExport(statement.exportClause) ||
        statement.exportClause.elements.some(element => !element.isTypeOnly))
    )
  }

  return ts.isImportEqualsDeclaration(statement) && !statement.isTypeOnly
}

function stronglyConnected(graph) {
  let nextIndex = 0
  const indices = new Map()
  const lowLinks = new Map()
  const stack = []
  const active = new Set()
  const components = new Map()

  function visit(filename) {
    const index = nextIndex++
    indices.set(filename, index)
    lowLinks.set(filename, index)
    stack.push(filename)
    active.add(filename)

    for (const { target } of graph.get(filename) ?? []) {
      if (!graph.has(target)) {
        continue
      }

      if (!indices.has(target)) {
        visit(target)
        lowLinks.set(filename, Math.min(lowLinks.get(filename), lowLinks.get(target)))
      } else if (active.has(target)) {
        lowLinks.set(filename, Math.min(lowLinks.get(filename), indices.get(target)))
      }
    }

    if (lowLinks.get(filename) === index) {
      const component = new Set()
      let member

      do {
        member = stack.pop()
        active.delete(member)
        component.add(member)
        components.set(member, component)
      } while (member !== filename)
    }
  }

  for (const filename of graph.keys()) {
    if (!indices.has(filename)) {
      visit(filename)
    }
  }

  return components
}

/** ESLint 的完整入口共用同一解析器与图；路径规则同时覆盖别名、相对引用和动态 import 字面量。 */
export function createArchitecturePlugin(clientDirectory) {
  const clientRoot = canonical(clientDirectory)
  const rendererRoot = `${clientRoot}/renderer/`
  const features = directories(path.join(clientDirectory, 'renderer/features'))
  const windows = directories(path.join(clientDirectory, 'renderer/app/windows'))
  const rendererProject = readProject(clientDirectory, 'tsconfig.json')
  const mainProject = readProject(clientDirectory, 'tsconfig.main.json')
  const rendererResolveCache = ts.createModuleResolutionCache(clientDirectory, canonical, rendererProject.options)
  const mainResolveCache = ts.createModuleResolutionCache(clientDirectory, canonical, mainProject.options)
  const graph = new Map()
  const sources = new Map()
  let components = null

  const optionsFor = filename =>
    filename.startsWith(`${clientRoot}/main/`) || filename.startsWith(`${clientRoot}/scripts/`)
      ? mainProject.options
      : rendererProject.options

  function resolve(specifier, filename) {
    const options = optionsFor(filename)
    const cache = options === mainProject.options ? mainResolveCache : rendererResolveCache
    const resolved = ts.resolveModuleName(specifier, filename, options, ts.sys, cache).resolvedModule

    return resolved ? canonical(resolved.resolvedFileName) : undefined
  }

  function domain(filename) {
    if (!filename.startsWith(rendererRoot)) {
      return filename.startsWith(`${clientRoot}/main/`)
        ? { main: filename.slice(`${clientRoot}/main/`.length).split('/')[0] }
        : {}
    }

    const relative = filename.slice(rendererRoot.length)
    const parts = relative.split('/')

    const isDirectory = (known, parent, name) => {
      if (
        name &&
        !known.has(name) &&
        fs.statSync(path.join(rendererRoot, parent, name), { throwIfNoEntry: false })?.isDirectory()
      ) {
        known.add(name)
      }

      return known.has(name)
    }

    return {
      relative,
      layer: parts[0],
      feature: parts[0] === 'features' && isDirectory(features, 'features', parts[1]) ? parts[1] : undefined,
      window:
        parts[0] === 'app' && parts[1] === 'windows' && isDirectory(windows, 'app/windows', parts[2])
          ? parts[2]
          : undefined,
      subdomain:
        parts[0] === 'features' && parts[1] === 'creation' && CREATION_SUBDOMAINS.has(parts[2]) ? parts[2] : undefined,
      rendering: relative.startsWith('features/character/rendering/'),
      coordination: relative.startsWith('app/runtime/') || relative.startsWith('app/workflows/')
    }
  }

  const mainRestrictions = {
    shared: ['backend', 'ipc', 'lifecycle', 'runner'],
    backend: ['runner', 'ipc', 'lifecycle'],
    runner: ['backend', 'ipc'],
    ipc: ['lifecycle'],
    lifecycle: ['runner']
  }

  function boundaryViolation(from, to, specifier) {
    if (mainRestrictions[from.main]?.includes(to.main)) {
      return `main/${from.main} 不得依赖 main/${to.main}；能力经组合根注入。`
    }

    if (from.layer && to.main) {
      return '渲染层不得依赖主进程实现；跨进程只经类型化 IPC 契约。'
    }

    if (from.main && to.layer) {
      return '主进程不得依赖渲染层实现。'
    }

    if (from.layer === 'shared' && (to.layer === 'ui' || to.layer === 'features' || to.layer === 'app')) {
      return 'shared 不得依赖 ui、业务功能或应用层。'
    }

    if (from.layer === 'ui' && (to.layer === 'features' || to.layer === 'app')) {
      return 'ui 只承载无业务依赖的组件；不得依赖业务功能或应用层。'
    }

    if (
      from.layer === 'ui' &&
      to.layer === 'shared' &&
      BUSINESS_REQUEST_MODULES.has(to.relative?.replace(/\.tsx?$/, ''))
    ) {
      return 'ui 不得自行发起业务请求；请求与状态归所属功能，组件只接收数据与回调。'
    }

    if (from.feature && to.layer === 'app' && to.relative.split('/')[1] !== 'navigation') {
      return '业务功能只可依赖 app/navigation 路由端口，不得依赖窗口、运行时与工作流实现。'
    }

    if (from.coordination && to.window) {
      return '运行时与工作流不得反向导入窗口组件。'
    }

    if (from.window && to.window && from.window !== to.window) {
      return '窗口体验互不依赖；共享能力归业务功能或 app/workflows。'
    }

    if (from.rendering && to.feature === 'character' && !to.rendering && !specifier.startsWith('.')) {
      return '角色渲染域只经 character 公共入口访问角色能力。'
    }

    if (from.feature && from.feature === to.feature && !specifier.startsWith('.')) {
      return '业务功能内部使用相对路径；角色渲染域经 character 公共入口访问角色能力。'
    }

    if (from.subdomain && to.subdomain && from.subdomain !== to.subdomain) {
      if (!to.relative.endsWith(`/${to.subdomain}/index.ts`)) {
        return '同一功能的子域各自独立；跨子域只消费对方 index.ts。'
      }
    }

    if (!to.feature || from.feature === to.feature) {
      return undefined
    }

    const entry = to.relative.endsWith('/index.ts') || to.relative.endsWith('/index.tsx')

    if (!entry && from.feature) {
      return '跨功能只消费对方 index.ts 公共入口，不得导入内部实现。'
    }

    return undefined
  }

  function runtimeEdges(filename, text) {
    const options = optionsFor(filename)
    const source = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true)
    // 先执行 TypeScript 的类型擦除，避免未显式写 type 的纯类型引用造成假环。
    const emitted = ts.transpileModule(text, {
      fileName: filename,
      compilerOptions: { ...options, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve }
    }).outputText
    const javascript = ts.createSourceFile(`${filename}.js`, emitted, ts.ScriptTarget.Latest, true)
    const runtimeSpecifiers = new Set(
      javascript.statements
        .map(staticModule)
        .filter(Boolean)
        .map(node => node.text)
    )
    const edges = []

    for (const statement of source.statements) {
      const specifier = staticModule(statement)

      if (!specifier || !isValueDependency(statement) || !runtimeSpecifiers.has(specifier.text)) {
        continue
      }

      const target = resolve(specifier.text, filename)

      if (
        target &&
        !target.includes('/node_modules/') &&
        /\.[cm]?tsx?$/.test(target) &&
        !/\.d\.[cm]?ts$/.test(target)
      ) {
        edges.push({ target, start: specifier.getStart(source), end: specifier.end })
      }
    }

    return edges
  }

  function updateGraph(filename, text) {
    if (sources.get(filename) === text) {
      return
    }

    sources.set(filename, text)
    const edges = runtimeEdges(filename, text)
    graph.set(filename, edges)
    components = null

    for (const { target } of edges) {
      if (!sources.has(target)) {
        const targetText = ts.sys.readFile(target)

        if (targetText !== undefined) {
          updateGraph(target, targetText)
        }
      }
    }
  }

  function ensureGraph(filename, text) {
    if (graph.size === 0) {
      for (const project of [rendererProject, mainProject]) {
        for (const projectFile of project.fileNames) {
          const file = canonical(projectFile)

          if (!/\.d\.[cm]?ts$/.test(file)) {
            const fileText = ts.sys.readFile(file)

            if (fileText !== undefined) {
              updateGraph(file, fileText)
            }
          }
        }
      }
    }

    updateGraph(filename, text)

    if (!components) {
      components = stronglyConnected(graph)
    }
  }

  function cyclePath(filename, target) {
    const component = components.get(filename)

    if (!component?.has(target)) {
      return undefined
    }

    const queue = [[target]]
    const visited = new Set()

    for (let index = 0; index < queue.length; index++) {
      const chain = queue[index]
      const current = chain.at(-1)

      if (current === filename) {
        return [filename, ...chain].map(file => path.relative(clientRoot, file).replaceAll('\\', '/')).join(' → ')
      }

      if (!visited.has(current)) {
        visited.add(current)

        for (const edge of graph.get(current) ?? []) {
          if (component.has(edge.target)) {
            queue.push([...chain, edge.target])
          }
        }
      }
    }

    return undefined
  }

  return {
    rules: {
      boundaries: {
        meta: { type: 'problem', schema: [], messages: { boundary: '{{reason}} 引用：{{specifier}}' } },
        create(context) {
          const filename = canonical(context.filename)
          const from = domain(filename)

          function check(node, source) {
            const specifier = literalString(source)
            const target = specifier && resolve(specifier, filename)
            const reason = target && boundaryViolation(from, domain(target), specifier)

            if (reason) {
              context.report({ node, messageId: 'boundary', data: { reason, specifier } })
            }
          }

          return {
            ImportDeclaration: node => check(node.source, node.source),
            ExportNamedDeclaration: node => node.source && check(node.source, node.source),
            ExportAllDeclaration: node => check(node.source, node.source),
            ImportExpression: node => check(node.source, node.source),
            TSImportType: node => check(node, node.argument?.literal),
            TSImportEqualsDeclaration: node => check(node, node.moduleReference?.expression)
          }
        }
      },
      'static-cycles': {
        meta: { type: 'problem', schema: [], messages: { cycle: '运行时静态值依赖成环：{{chain}}' } },
        create(context) {
          const filename = canonical(context.filename)

          if (/\.d\.[cm]?ts$/.test(filename)) {
            return {}
          }

          return {
            'Program:exit': () => {
              ensureGraph(filename, context.sourceCode.text)

              for (const edge of graph.get(filename) ?? []) {
                const chain = cyclePath(filename, edge.target)

                if (chain) {
                  context.report({
                    loc: {
                      start: context.sourceCode.getLocFromIndex(edge.start),
                      end: context.sourceCode.getLocFromIndex(edge.end)
                    },
                    messageId: 'cycle',
                    data: { chain }
                  })
                }
              }
            }
          }
        }
      },
      'ui-business-calls': {
        meta: {
          type: 'problem',
          schema: [],
          messages: { business: 'ui 只承载展示与交互；业务请求归所属功能，组件只接收数据与回调。' }
        },
        create(context) {
          const filename = canonical(context.filename)

          if (!filename.startsWith(`${rendererRoot}ui/`)) {
            return {}
          }

          const reported = new Set()

          return {
            Identifier(node) {
              if (!BUSINESS_REQUEST_HELPERS.has(node.name)) {
                return
              }

              // 同一位置的多个绑定节点（导入名/本地名、简写属性键值）只报一次。
              const anchor = `${node.range[0]}:${node.range[1]}`

              if (!reported.has(anchor)) {
                reported.add(anchor)
                context.report({ node, messageId: 'business' })
              }
            }
          }
        }
      },
      'platform-access': {
        meta: {
          type: 'problem',
          schema: [],
          messages: { platform: 'preload 全局桥只由 shared/platform/desktop.ts 访问；消费所属能力端口。' }
        },
        create(context) {
          const filename = canonical(context.filename)

          if (
            [`${rendererRoot}shared/platform/desktop.ts`, `${rendererRoot}shared/types/global.d.ts`].includes(filename)
          ) {
            return {}
          }

          const bridges = new Set(['spiritagent', 'desktopBackground', 'spiritagentWebUtils'])

          function isWindow(node, visited = new Set()) {
            if (!node || visited.has(node)) {
              return false
            }

            visited.add(node)

            if (['ChainExpression', 'TSAsExpression', 'TSNonNullExpression', 'TSTypeAssertion'].includes(node.type)) {
              return isWindow(node.expression, visited)
            }

            if (node.type === 'MemberExpression') {
              return ['window', 'self', 'globalThis'].includes(propertyName(node)) && isWindow(node.object, visited)
            }

            if (node.type !== 'Identifier') {
              return false
            }

            let scope = context.sourceCode.getScope(node)
            let variable

            while (scope && !variable) {
              variable = scope.set.get(node.name)
              scope = scope.upper
            }

            if (!variable?.defs.length) {
              return ['window', 'self', 'globalThis'].includes(node.name)
            }

            return variable.defs.some(
              definition => definition.node.type === 'VariableDeclarator' && isWindow(definition.node.init, visited)
            )
          }

          return {
            MemberExpression(node) {
              if (bridges.has(propertyName(node)) && isWindow(node.object)) {
                context.report({ node, messageId: 'platform' })
              }
            },
            ObjectPattern(node) {
              const parent = node.parent
              const source =
                parent.type === 'VariableDeclarator'
                  ? parent.init
                  : parent.type === 'AssignmentExpression'
                    ? parent.right
                    : undefined

              if (isWindow(source)) {
                for (const property of node.properties) {
                  const key =
                    property.type === 'Property' ? (property.key.name ?? literalString(property.key)) : undefined

                  if (bridges.has(key)) {
                    context.report({ node: property, messageId: 'platform' })
                  }
                }
              }
            },
            TSQualifiedName(node) {
              if (bridges.has(node.right.name) && isWindow(node.left)) {
                context.report({ node, messageId: 'platform' })
              }
            },
            TSIndexedAccessType(node) {
              if (
                node.objectType.type === 'TSTypeReference' &&
                node.objectType.typeName.name === 'Window' &&
                bridges.has(literalString(node.indexType.literal))
              ) {
                context.report({ node, messageId: 'platform' })
              }
            },
            TSInterfaceDeclaration(node) {
              if (node.id.name === 'Window') {
                for (const property of node.body.body) {
                  if (bridges.has(property.key?.name ?? literalString(property.key))) {
                    context.report({ node: property, messageId: 'platform' })
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
