import fs from 'node:fs'

// Evaluate real, local composable modules with this test's Vue hooks/clock/API.
// Nothing substitutes the production state, queue, filters or chart logic.
export function loadComposable(file, deps, cache = new Map()) {
  if (cache.has(file.href)) return cache.get(file.href)
  let source = fs.readFileSync(file, 'utf8')
  const scope = { ...deps }
  for (const match of source.matchAll(/^import\s+\{([^}]+)\}\s+from\s+['"](\.[^'"]+)['"]/gm)) {
    const names = match[1].split(',').map(name => name.trim())
    if (names.every(name => name in scope)) continue
    Object.assign(scope, loadComposable(new URL(match[2], file), deps, cache))
  }
  const exports = [...source.matchAll(/^export\s+(?:async\s+)?(?:function|const)\s+(\w+)/gm)].map(match => match[1])
  source = source.replace(/^import .*$/gm, '').replace(/^export /gm, '')
  const entries = Object.entries(scope).filter(([key]) => /^[A-Za-z_$][\w$]*$/.test(key) && key !== 'default')
  const result = new Function(...entries.map(([key]) => key), `${source}; return { ${exports.join(', ')} }`)(...entries.map(([,value]) => value))
  cache.set(file.href, result)
  return result
}
