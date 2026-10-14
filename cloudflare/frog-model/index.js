import module from './build/frog_model.wasm'
import { wasmPlanner } from './model.js'
import { createWorker } from './worker.js'
const instance = new WebAssembly.Instance(module, {})
export default createWorker(wasmPlanner(instance))
