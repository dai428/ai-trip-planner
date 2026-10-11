/**
 * 一次性校验：行程结果页的标题区是否真的渲染出了「编排来源」标签。
 *
 * 【为什么要单独验这一下】
 *   后端的 generator 字段有了、类型定义也改了，
 *   但"模板里到底有没有把它显示出来"是另一回事 ——
 *   字段接错、条件写反、类名不存在，都不会报错，只会静静地不显示。
 *   所以直接读 DOM 文本，比看截图更确定。
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CDP_PORT = Number(process.env.CDP_PORT || 9333)
const APP = process.env.APP_URL || 'http://127.0.0.1:5273'
const TRIP_ID = process.argv[2]

function findBrowser() {
  const c = [
    process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  ].filter(Boolean)
  const f = c.find((p) => existsSync(p))
  if (!f) throw new Error('没找到浏览器')
  return f
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let ws, seq = 0
const pending = new Map()

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
}

const chrome = spawn(findBrowser(), [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`,
  `--user-data-dir=${join(tmpdir(), 'xj-chip-profile')}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank',
], { stdio: 'ignore' })

try {
  let target = null
  for (let i = 0; i < 40 && !target; i++) {
    await sleep(300)
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()
      target = list.find((t) => t.type === 'page')
    } catch { /* 还没起来 */ }
  }
  if (!target) throw new Error('浏览器没起来')

  ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((r) => { ws.onopen = r })
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id)
      pending.delete(m.id)
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result)
    }
  }

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Page.navigate', { url: `${APP}/trip/${TRIP_ID}` })
  await sleep(3500)

  const r = await send('Runtime.evaluate', {
    expression: `JSON.stringify({
      chips: [...document.querySelectorAll('.chip')].map(e => e.textContent.trim()),
      hints: [...document.querySelectorAll('.section-hint')].map(e => e.textContent.trim()),
      days: document.querySelectorAll('.day, [class*=day]').length
    })`,
    returnByValue: true,
  })
  const data = JSON.parse(r.result.value)

  console.log('页面上的 chip 文案：')
  data.chips.forEach((c) => console.log('   •', c))
  console.log('\n提示文案：')
  data.hints.slice(0, 3).forEach((h) => console.log('   •', h.slice(0, 90)))

  const hasLlmChip = data.chips.some((c) => c.includes('AI 编排'))
  const hasRuleChip = data.chips.some((c) => c.includes('规则生成'))
  console.log(`\n来源标签: ${hasLlmChip ? '✓ 显示「AI 编排」' : hasRuleChip ? '✓ 显示「规则生成」' : '✗ 没有找到'}`)
  process.exitCode = hasLlmChip || hasRuleChip ? 0 : 1
} finally {
  try { ws?.close() } catch { /* ignore */ }
  chrome.kill()
}
