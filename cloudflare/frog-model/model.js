const encoder = new TextEncoder(), decoder = new TextDecoder()
// One bounded synchronous call; no retained pointer/view across await or growth.
export function wasmPlanner(instance) {
  return request => {
    const input = encoder.encode(JSON.stringify(request))
    if (input.byteLength > 16384) throw Error('request_too_large')
    const api = instance.exports
    const pointer = api.frog_input_ptr()
    new Uint8Array(api.memory.buffer, pointer, input.length).set(input)
    const status = api.frog_plan(input.length)
    const result = JSON.parse(decoder.decode(new Uint8Array(api.memory.buffer, api.frog_output_ptr(), api.frog_output_len())))
    if (status !== 0) throw Error('invalid_request')
    return result
  }
}
