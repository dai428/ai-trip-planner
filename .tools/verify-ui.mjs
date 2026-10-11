/**
 * 用 Chrome DevTools Protocol 驱动真实浏览器跑一遍关键交互并截图。
 *
 * 【为什么不用 --screenshot 直接截】
 *    --screenshot 只能截静态首屏。而这次新增的能力
 *    （搜索城市、规划路线、采纳推荐）全都要先输入、等异步、再点击，
 *    静态截图根本走不到那些状态 —— 等于没验证。
 *    Node 22 自带 WebSocket，所以直接连 CDP，不引任何依赖。
 *
 * 【为什么浏览器要由本脚本自己拉起】
 *    Chrome 是子进程。若在 shell 里后台启动再另开脚本驱动，
 *    宿主 shell 一退出浏览器就被回收，端口直接拒连。
 *    同生共死才不会踩这个坑。
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CDP_PORT = Number(process.env.CDP_PORT || 9222)
const CDP = `http://127.0.0.1:${CDP_PORT}`
const APP = process.env.APP_URL || 'http://127.0.0.1:5273'
const OUT = process.argv[2] || join(tmpdir(), 'xingji-shots')
const PROFILE = join(tmpdir(), 'xj-cdp-profile')

/** 跨平台找一个可用的 Chrome/Edge，避免把某个人的绝对路径写死在脚本里 */
function findBrowser() {
  const candidates = [
    process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
  ].filter(Boolean)
  const found = candidates.find((p) => existsSync(p))
  if (!found) throw new Error('没找到 Chrome/Edge，可用 CHROME_PATH 环境变量指定')
  return found
}

let ws
let seq = 0
const pending = new Map()
let chromeProc = null

function send(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params, sessionId }))
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function evaluate(expression) {
  const r = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (r.exceptionDetails) {
    throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails))
  }
  return r.result?.value
}

async function shot(name, fullPage = true) {
  let clip
  if (fullPage) {
    const m = await send('Page.getLayoutMetrics')
    const h = Math.min(Math.ceil(m.cssContentSize.height), 6000)
    clip = { x: 0, y: 0, width: Math.ceil(m.cssContentSize.width), height: h, scale: 1 }
  }
  const opts = { format: 'png', captureBeyondViewport: fullPage }
  if (clip) opts.clip = clip
  const r = await send('Page.captureScreenshot', opts)
  const path = `${OUT}/${name}.png`
  const buf = Buffer.from(r.data, 'base64')
  writeFileSync(path, buf)
  console.log('已保存', path, `(${Math.round(buf.length / 1024)} KB)`)
}

/** 往 Vue 的 v-model 输入框里赋值并触发响应 */
async function typeInto(selector, text, index = 0) {
  const ok = await evaluate(`(() => {
    const els = document.querySelectorAll(${JSON.stringify(selector)});
    const el = els[${index}];
    if (!el) return false;
    el.focus();
    el.value = ${JSON.stringify(text)};
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()`)
  if (!ok) throw new Error(`找不到元素: ${selector} [${index}]`)
}

/** 按文字点一个按钮 */
async function clickByText(text, tag = 'button') {
  const ok = await evaluate(`(() => {
    const el = [...document.querySelectorAll(${JSON.stringify(tag)})]
      .find(e => e.textContent.trim() === ${JSON.stringify(text)});
    if (!el) return false;
    el.click();
    return true;
  })()`)
  if (!ok) throw new Error(`找不到按钮: ${text}`)
}

async function waitFor(expression, label, timeout = 20000) {
  const started = Date.now()
  while (Date.now() - started < timeout) {
    if (await evaluate(expression)) return true
    await sleep(200)
  }
  throw new Error(`等待超时: ${label}`)
}

/** 拉起 headless Chrome 并等调试端口可用 */
async function launchChrome() {
  const chrome = findBrowser()
  rmSync(PROFILE, { recursive: true, force: true })
  mkdirSync(PROFILE, { recursive: true })
  mkdirSync(OUT, { recursive: true })

  chromeProc = spawn(chrome, [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${PROFILE}`,
    'about:blank',
  ], { stdio: 'ignore' })

  for (let i = 0; i < 40; i++) {
    await sleep(500)
    try {
      const r = await fetch(`${CDP}/json/version`)
      if (r.ok) return
    } catch { /* 还没起来，继续等 */ }
  }
  throw new Error('Chrome 调试端口未就绪')
}

async function main() {
  await launchChrome()
  const list = await (await fetch(`${CDP}/json/list`)).json()
  const page = list.find((t) => t.type === 'page')
  if (!page) throw new Error('没有可用的页面 target')

  ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  }

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  })

  // 收集页面报错，静默的 JS 错误必须暴露出来
  const errors = []
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data)
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text)
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push(m.params.args.map((a) => a.value).join(' '))
    }
  })

  // ---------- 1. 首页 · 选城市 ----------
  await send('Page.navigate', { url: APP })
  await sleep(2500)
  await waitFor(`!!document.querySelector('.city-grid .city-card')`, '城市卡片渲染')
  await shot('01-home')

  // ---------- 2. 城市搜索（攻略库外的城市） ----------
  await typeInto('.search-input', '张家界', 0)
  await waitFor(`!!document.querySelector('.search-option')`, '搜索候选出现')
  const opts = await evaluate(`[...document.querySelectorAll('.search-option')].map(e => e.textContent.trim())`)
  console.log('搜索"张家界"候选:', JSON.stringify(opts))
  await shot('02-search')
  await evaluate(`document.querySelector('.search-option').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))`)
  await sleep(400)
  const picked = await evaluate(`[...document.querySelectorAll('.picked-item')].map(e => e.textContent.trim())`)
  console.log('已选列表:', JSON.stringify(picked))

  // ---------- 3. 切到路线模式 ----------
  await clickByText('规划路线')
  await sleep(600)
  await waitFor(`document.querySelectorAll('.search-input').length >= 2`, '路线模式输入框')
  await shot('03-route-empty')

  // ---------- 4. 填起终点并规划 ----------
  // 注意：候选面板是每个搜索框各一个，取 .search-option 时要限定在对应面板内，
  // 否则会点错到另一个框的候选上。
  async function fillSearch(index, city) {
    await typeInto('.search-input', city, index)
    await sleep(900)
    const panelInfo = await evaluate(`(() => {
      const panels = [...document.querySelectorAll('.search-panel')];
      return panels.map(p => ({ count: p.querySelectorAll('.search-option').length,
                                text: p.textContent.trim().slice(0, 60) }));
    })()`)
    console.log(`  输入「${city}」后可见面板:`, JSON.stringify(panelInfo))

    const clicked = await evaluate(`(() => {
      // 面板顺序与输入框顺序一致；找到"归属"该输入框的那个面板
      const wrap = document.querySelectorAll('.search-box')[${index}];
      const panel = wrap?.querySelector('.search-panel');
      const opt = panel?.querySelector('.search-option');
      if (!opt) return false;
      opt.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      return true;
    })()`)
    console.log(`  点击「${city}」候选:`, clicked)
    await sleep(500)
  }

  await fillSearch(0, '北京')
  await fillSearch(1, '大理')

  const filled = await evaluate(`[...document.querySelectorAll('.search-input')].map(e => e.value)`)
  console.log('输入框值:', JSON.stringify(filled))

  const btnState = await evaluate(`(() => {
    const b = document.querySelector('.route-submit');
    return b ? { text: b.textContent.trim(), disabled: b.disabled } : null;
  })()`)
  console.log('提交按钮:', JSON.stringify(btnState))

  if (btnState?.disabled) throw new Error('起终点未成功选中，提交按钮仍是禁用状态')

  await evaluate(`document.querySelector('.route-submit').click()`)
  await waitFor(`!!document.querySelector('.route-chain') || !!document.querySelector('.alert')`, '路线结果或错误提示', 30000)
  await sleep(800)

  const alertText = await evaluate(`document.querySelector('.alert')?.textContent.trim() || null`)
  if (alertText) throw new Error(`后端返回错误: ${alertText}`)

  const summary = await evaluate(`(() => {
    const chips = [...document.querySelectorAll('.route-verdict .chip')].map(e => e.textContent.trim());
    const nodes = [...document.querySelectorAll('.route-node-name')].map(e => e.textContent.trim());
    const wps = [...document.querySelectorAll('.waypoint-card')].map(e => e.textContent.trim().replace(/\\s+/g,' '));
    return { chips, nodes, wps };
  })()`)
  console.log('路线结果:', JSON.stringify(summary, null, 2))
  await shot('04-route-result')

  // ---------- 5. 一键采纳 → 生成行程 ----------
  await clickByText('按这条路线生成行程')
  await waitFor(`location.pathname.startsWith('/trip/')`, '跳转到行程页', 40000)
  await sleep(1800)
  await waitFor(`!!document.querySelector('.timeline .day-item')`, '行程时间轴渲染')
  const days = await evaluate(`document.querySelectorAll('.timeline .day-item').length`)
  console.log('生成的行程天数:', days)
  const tripDays = await evaluate(`[...document.querySelectorAll('.day-city')].map(e => e.textContent.trim())`)
  console.log('每日城市:', JSON.stringify(tripDays))
  await shot('05-trip-result')

  // ---------- 6. 深色主题 ----------
  await evaluate(`document.documentElement.dataset.theme = 'dark'`)
  await sleep(600)
  await shot('06-dark')

  if (errors.length) {
    console.log('\n⚠ 页面 JS 报错:')
    for (const e of errors) console.log('  -', e.split('\n')[0])
    process.exitCode = 1
  } else {
    console.log('\n没有捕获到页面 JS 报错')
  }
}

main()
  .catch((e) => {
    console.error('失败:', e.message)
    process.exitCode = 1
  })
  .finally(() => {
    try { ws?.close() } catch { /* ignore */ }
    try { chromeProc?.kill() } catch { /* ignore */ }
    setTimeout(() => process.exit(process.exitCode ?? 0), 300)
  })

