// PostToolUse hook: after the agent edits code, run the relevant fast check and
// feed failures back to it (exit code 2 => stderr is shown to the model).
import { execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

let input = ''
process.stdin.on('data', (c) => (input += c))
process.stdin.on('end', () => {
  let file = ''
  try {
    const data = JSON.parse(input)
    file = data.tool_input?.file_path ?? ''
  } catch {
    process.exit(0)
  }
  const root = resolve(process.env.CLAUDE_PROJECT_DIR ?? '.')
  const norm = resolve(file).replaceAll('\\', '/')
  const rootN = root.replaceAll('\\', '/')

  let cmd = null
  let cwd = null
  if (norm.startsWith(`${rootN}/backend/`) && norm.endsWith('.py')) {
    const py = join(root, 'backend', '.venv', 'Scripts', 'python.exe')
    cmd = `"${existsSync(py) ? py : 'python'}" -m pytest -q -x --no-header -p no:warnings`
    cwd = join(root, 'backend')
  } else if (norm.startsWith(`${rootN}/frontend/src/`) && /\.(ts|tsx)$/.test(norm)) {
    cmd = 'npx tsc -b'
    cwd = join(root, 'frontend')
  }
  if (!cmd) process.exit(0)

  try {
    execSync(cmd, { cwd, stdio: 'pipe', timeout: 120_000 })
  } catch (e) {
    const out = `${e.stdout ?? ''}${e.stderr ?? ''}`.split('\n').slice(-40).join('\n')
    process.stderr.write(`Автопроверка после правки ${file} упала (${cmd}):\n${out}\n`)
    process.exit(2)
  }
})
