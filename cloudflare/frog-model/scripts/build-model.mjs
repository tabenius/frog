import { spawnSync } from 'node:child_process'
import { mkdirSync, copyFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../../../', import.meta.url))
const native = spawnSync('cargo', ['build', '--locked', '--bin', 'frog-model-plan'], { cwd: root + 'rust/frog-model', stdio: 'inherit' })
if (native.status !== 0) process.exit(native.status || 1)
const result = spawnSync('cargo', ['build', '--locked', '--release', '--lib', '--target', 'wasm32-unknown-unknown'], { cwd: root + 'rust/frog-model', stdio: 'inherit' })
if (result.status !== 0) process.exit(result.status || 1)
const build = fileURLToPath(new URL('../build/', import.meta.url))
mkdirSync(build, { recursive: true })
copyFileSync(root + 'rust/frog-model/target/wasm32-unknown-unknown/release/frog_model.wasm', build + 'frog_model.wasm')
const migrations = fileURLToPath(new URL('../migrations/', import.meta.url))
mkdirSync(migrations, { recursive: true })
copyFileSync(root + 'rust/frog-model/sql/0001_model.sql', migrations + '0001_model.sql')
