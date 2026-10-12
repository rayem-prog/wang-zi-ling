import { client } from './client.js'

// One short HTTP submission followed by short polls; retries keep the same ID.
export async function runSandboxJob(operation, params = {}, options = {}) {
  const requestId = globalThis.crypto.randomUUID()
  const body = { request_id: requestId, operation, params, days: options.days || 1 }
  let job
  try {
    job = (await client.post('/sandbox/jobs', body)).data
  } catch (error) {
    if (error.response) throw error
    job = (await client.post('/sandbox/jobs', body)).data
  }
  return await waitSandboxJob(job, options)
}

export async function waitSandboxJob(job, options = {}) {
  while (job.status === 'queued' || job.status === 'running') {
    if (options.cancelled?.()) return null
    options.onProgress?.(job.progress)
    await new Promise(resolve => setTimeout(resolve, options.pollInterval ?? 700))
    if (options.cancelled?.()) return null
    try {
      job = (await client.get(`/sandbox/jobs/${encodeURIComponent(job.job_id)}`)).data
    } catch (error) {
      if (error.response && error.response.status < 500) throw error
      options.onProgress?.({ message: '连接暂时中断，正在重新查询同一后台任务', completed: 0, total: 0 })
      // The server may still be executing: retry the poll, never the mutation.
    }
  }
  if (job.status === 'failed') throw new Error(job.error || '后台任务失败')
  return job.result
}
