import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import * as vue from 'vue'
import { client } from '../src/api/client.js'
import { signalsApi } from '../src/api/signals.js'
import { loadComposable } from './helpers/module-loader.mjs'

const fixtures = JSON.parse(fs.readFileSync(new URL('../../docs/work-package-a-20261012/contract-examples.json', import.meta.url), 'utf8'))
const clone = name => structuredClone(fixtures[name])
const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve() }
function deferred() { let resolve; const promise=new Promise(yes=>{resolve=yes}); return {promise,resolve} }
function setup(api) {
  const timers=new Map(), unmounted=[], emitted=[]
  let sequence=0
  const deps={...vue, api:{getWatchlist:async()=>[],...api},
    onMounted(){}, onUnmounted(fn){unmounted.push(fn)},
    setTimeout(fn){const id=++sequence;timers.set(id,fn);return id}, clearTimeout(id){timers.delete(id)},
    window:{addEventListener(){},removeEventListener(){}}}
  const {useSignalsPage}=loadComposable(new URL('../src/features/signals/useSignalsPage.js',import.meta.url),deps)
  const page=useSignalsPage((...args)=>emitted.push(args))
  return {page,timers,emitted,dispose(){unmounted.forEach(fn=>fn())}}
}

test('snapshot API gets rows, counts and state from one endpoint',async()=>{
  const previous=client.defaults.adapter, requests=[]
  client.defaults.adapter=async config=>{requests.push(config.url);return {data:clone('valid'),status:200,headers:{},config}}
  try {
    const result=await signalsApi.getSignalsSnapshot({sort_by:'score'})
    assert.equal(result.snapshot_id,'S1')
    assert.equal(result.counts.filtered_total,1)
    assert.deepEqual(requests,['/signals/snapshot'])
  } finally {client.defaults.adapter=previous}
})

test('updating retains the dated ranking and same response counts',async()=>{
  let calls=0
  const t=setup({getSignalsSnapshot:async()=>clone(++calls===1?'valid':'updating')})
  try {
    await t.page.loadSignals(); await t.page.loadSignals()
    assert.equal(t.page.signals.value[0].code,'000001')
    assert.equal(t.page.stats.value.total_count,1)
    assert.equal(t.page.snapshot.value.snapshot_id,'S1')
    assert.equal(t.page.snapshot.value.data_as_of,'2026-10-09T15:00:00+08:00')
    assert.equal(t.page.canBuy.value,false)
    assert.match(t.page.statusMessage.value,/核验|更新/)
    assert.equal(t.timers.size,1)
  } finally {t.dispose()}
})

test('connection failure retains content but pauses decisions and explains failure',async()=>{
  let calls=0
  const t=setup({getSignalsSnapshot:async()=>{if(++calls>1)throw new Error('connection lost');return clone('valid')}})
  try {
    await t.page.loadSignals();await t.page.loadSignals()
    assert.equal(t.page.signals.value.length,1)
    assert.equal(t.page.snapshot.value.result_state,'historical')
    assert.equal(t.page.snapshot.value.action_permissions.create_buy,'paused')
    assert.match(t.page.statusMessage.value,/connection lost/)
    t.page.handleGoPaper(t.page.signals.value[0]);assert.equal(t.emitted.length,0)
    assert.equal(t.timers.size,1)
  } finally {t.dispose()}
})

test('older responses cannot replace a newer version or its counts',async()=>{
  const older=deferred(), newer=deferred();let calls=0
  const t=setup({getSignalsSnapshot:()=>++calls===1?older.promise:newer.promise})
  try {
    const first=t.page.loadSignals(),second=t.page.loadSignals()
    const s2=clone('valid');s2.snapshot_id='S2';s2.response_revision=20;s2.rows[0].code='000002'
    newer.resolve(s2);await second
    older.resolve(clone('valid'));await first
    assert.equal(t.page.signals.value[0].code,'000002')
    assert.equal(t.page.snapshot.value.snapshot_id,'S2')
    assert.equal(t.page.stats.value.total_count,1)
  } finally {t.dispose()}
})

test('failed update remains visible with retained rows; revoked response removes usable rows',async()=>{
  let calls=0
  const t=setup({getSignalsSnapshot:async()=>clone(++calls===1?'failed':'revoked')})
  try {
    await t.page.loadSignals()
    assert.equal(t.page.signals.value.length,1)
    assert.match(t.page.statusMessage.value,/行情源连接失败/)
    await t.page.loadSignals()
    assert.equal(t.page.signals.value.length,0)
    assert.equal(t.page.canBuy.value,false)
    assert.match(t.page.statusMessage.value,/撤销/)
    t.page.handleGoPaper({code:'000001'});assert.equal(t.emitted.length,0)
  } finally {t.dispose()}
})

test('legitimate empty publication replaces old results with an explained empty state',async()=>{
  let calls=0
  const t=setup({getSignalsSnapshot:async()=>clone(++calls===1?'valid':'normal_empty')})
  try {
    await t.page.loadSignals();await t.page.loadSignals()
    assert.equal(t.page.signals.value.length,0)
    assert.equal(t.page.snapshot.value.empty_kind,'screened_out')
    assert.match(t.page.statusMessage.value,/筛选|候选/)
    assert.equal(t.page.stats.value.total_count,0)
  } finally {t.dispose()}
})

test('dispose clears retry timers and ignores in-flight results',async()=>{
  const waiting=deferred()
  const t=setup({getSignalsSnapshot:()=>waiting.promise})
  const read=t.page.loadSignals();t.dispose();waiting.resolve(clone('valid'));await read;await flush()
  assert.equal(t.page.signals.value.length,0)
  assert.equal(t.timers.size,0)
})

async function renderView(page) {
  const {parse,compileScript,compileTemplate}=await import('@vue/compiler-sfc')
  const {renderToString}=await import('@vue/server-renderer')
  const file=new URL('../src/views/SignalsView.vue',import.meta.url)
  const {descriptor}=parse(fs.readFileSync(file,'utf8'))
  const script=compileScript(descriptor,{id:'signals-test'})
  const dependencies={useSignalsPage:()=>page, KLineModal:{render:()=>null},StockAttributionModal:{render:()=>null}}
  const code=script.content.replace(/^import .*$/gm,'').replace('export default','return')
  const component=new Function(...Object.keys(dependencies),code)(...Object.values(dependencies))
  const state=component.setup({systemStatus:{}},{expose(){},emit(){}})
  const template=compileTemplate({source:descriptor.template.content,filename:'SignalsView.vue',id:'signals-test',compilerOptions:{bindingMetadata:script.bindings}}).code
    .replace(/import \{(.*?)\} from "vue"/s,(_,names)=>`const { ${names.replace(/ as /g,': ')} } = Vue`)
    .replace('export function render','function render')
  const render=new Function('Vue',`${template}; return render`)(vue)
  return renderToString(vue.createSSRApp({setup:()=>state,render}))
}

test('dated status and update failure are rendered while ranking rows remain visible',async()=>{
  const t=setup({getSignalsSnapshot:async()=>clone('failed')})
  try {
    await t.page.loadSignals()
    const html=await renderView(t.page)
    assert.match(html,/固定样本/)
    assert.match(html,/2026[/-]10[/-]0?9/)
    assert.match(html,/行情源连接失败/)
    assert.match(html,/历史结果/)
    assert.match(html,/disabled/)
  } finally {t.dispose()}
})
