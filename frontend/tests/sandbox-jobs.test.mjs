import test from 'node:test'
import assert from 'node:assert/strict'
import { client } from '../src/api/client.js'
import { waitSandboxJob } from '../src/api/sandboxJobs.js'

test('transient poll failure retries the same job without resubmitting a step', async () => {
  const old = client.defaults.adapter
  let polls = 0
  client.defaults.adapter = async config => {
    assert.equal(config.method, 'get')
    assert.equal(config.url, '/sandbox/jobs/one')
    polls++
    if (polls === 1) throw new Error('temporary disconnect')
    return {data:{job_id:'one',status:'succeeded',result:{current_step:10}},status:200,statusText:'OK',headers:{},config}
  }
  try {
    assert.deepEqual(await waitSandboxJob({job_id:'one',status:'running'}, {pollInterval:0}), {current_step:10})
    assert.equal(polls, 2)
  } finally { client.defaults.adapter = old }
})

test('missing jobs stop polling with an actionable error', async () => {
  const old = client.defaults.adapter
  client.defaults.adapter = async () => { throw Object.assign(new Error('missing'),{response:{status:404}}) }
  try {
    await assert.rejects(waitSandboxJob({job_id:'missing',status:'running'},{pollInterval:0}),/missing/)
  } finally { client.defaults.adapter = old }
})
