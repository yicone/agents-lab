"""ACM-615 fixture-only acceptance, using the installed ego-browser.

python3 multica-board/web/density-acceptance.py --baseline /path/to/baseline/web --out /path/to/artifacts
Only fixture servers are used; runtime catalog is read without preview/execute.
No live CLI collection or configuration changes. Requires Pillow for comparisons.
"""
from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import select
import sys
import threading
from urllib.parse import urlsplit

from PIL import Image, ImageChops


SCRIPT = r"""
const fs = await import('node:fs/promises');
const task = await taskSpace('ACM-615 acceptance');
const page = task.page('p1');
const checks = [];
const metrics = {};
function check(label, pass, detail = '') { checks.push({label, pass:!!pass, detail}); console.log(`${pass ? 'PASS' : 'FAIL'} ${label} ${detail}`); }
async function open(side, mock, hash, selector) {
  await page.goto(`${config.base}/${side}/?mock=${mock}#/${hash}`);
  await page.waitForSelector(selector);
  await page.cdp('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
}
async function snap(name, note = '') {
  if (note) await page.evaluate(text => { const n = document.createElement('div'); n.id='acceptance-note'; n.textContent=text; Object.assign(n.style,{position:'fixed',bottom:'0',left:'0',zIndex:'9999',background:'var(--panel)',color:'var(--text)',border:'1px solid var(--line)',padding:'6px 10px',font:'12px monospace'});document.body.append(n); }, note);
  if(name.includes('-S4-')) await page.evaluate(()=>{const r=document.querySelector('.rt').getBoundingClientRect();const line=document.createElement('div');line.id='acceptance-edge';Object.assign(line.style,{position:'fixed',left:`${r.left}px`,top:`${Math.max(0,r.top-80)}px`,bottom:'0',borderLeft:'2px solid var(--accent)',zIndex:'9998',pointerEvents:'none'});document.body.append(line);});
  await page.screenshot({path:`${config.out}/${name}.png`});
  await page.evaluate(()=>document.querySelector('#acceptance-edge')?.remove());
  if (note) await page.evaluate(()=>document.querySelector('#acceptance-note').remove());
}
async function topbar(name) {
  const clip = await page.evaluate(()=>{const r=document.querySelector('.topbar').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scale:1};});
  const {data}=await page.cdp('Page.captureScreenshot',{format:'png',clip});
  await fs.writeFile(`${config.out}/${name}.png`,Buffer.from(data,'base64'));
}
async function rowMetrics() {
  return await page.evaluate(()=>({
    y:document.querySelector('.skill-row').getBoundingClientRect().top,
    rows:[...document.querySelectorAll('.skill-row')].filter(n=>{const r=n.getBoundingClientRect();return r.top>=0 && r.bottom<=900;}).length,
    width:document.querySelector('.table-wrap').clientWidth,
    scrollWidth:document.querySelector('.table-wrap').scrollWidth,
    queueWidth:document.querySelector('.skills-queue').getBoundingClientRect().width,
    padding:getComputedStyle(document.querySelector('.skill-row td')).padding,
    sticky:getComputedStyle(document.querySelector('.skills-table thead th')).position,
  }));
}
async function warningStyle() {
  return await page.evaluate(()=>{const s=getComputedStyle(document.querySelector('#main .decide-note, #main .skills-anomaly'));return {padding:s.padding,border:s.borderWidth,radius:s.borderRadius,font:s.fontSize};});
}
for (const theme of ['light','dark']) {
  await page.cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:theme}]});
  // Reset only this script's preferences, on this isolated fixture origin.
  await open('after','1','configure/skills','.skill-row');
  await page.evaluate(()=>{for (const key of ['legend','sources','columns']) localStorage.removeItem(`aa.skills.${key}`);});
  await open('before','1','configure/skills','.skill-row');
  const before = await rowMetrics();
  await topbar(`${theme}-topbar-before`);
  await snap(`${theme}-S7-before`, `1440x900 | first row y=${before.y.toFixed(1)} | visible rows=${before.rows}`);
  await open('after','1','configure/skills','.skill-row');
  const after = await rowMetrics();
  metrics[theme] = {before,after};
  await topbar(`${theme}-topbar-after`);
  await snap(`${theme}-S7-after`, `1440x900 | first row y=${after.y.toFixed(1)} | visible rows=${after.rows} | gain=${((after.rows/before.rows-1)*100).toFixed(0)}%`);
  check(`${theme}: density >=30%`, before.rows>0 && after.rows>=before.rows*1.3, JSON.stringify({before,after}));
  check(`${theme}: default table no horizontal scroll`, after.scrollWidth<=after.width);
  check(`${theme}: padding, sticky header, queue width`, after.padding==='6px 8px' && after.sticky==='sticky' && after.queueWidth===352);
  const skillsWarning = await warningStyle();
  await snap(`${theme}-S2-skills`, `Skills warning: ${JSON.stringify(skillsWarning)}`);

  for (const side of ['before','after']) {
    await open(side,'acm-stale','running/agents','.agent-row');
    await snap(`${theme}-S1-${side}`, 'Isolated fixture: one stale source; other sources healthy');
  }
  const statusWarning = await warningStyle();
  check(`${theme}: same warning geometry`, JSON.stringify(skillsWarning)===JSON.stringify(statusWarning));
  check(`${theme}: stale source summarized once`, await page.evaluate(()=>document.querySelectorAll('#main .decide-note').length===1 && ![...document.querySelectorAll('.agent-row .tag')].some(n=>n.textContent==='旧数据')));
  await page.click('#main .decide-note summary');
  check(`${theme}: source timestamps expandable`, await page.evaluate(()=>document.querySelector('#main .decide-note').open));
  await snap(`${theme}-S1-source-detail`);
  await open('after','acm-clean','running/agents','.agent-row');
  check(`${theme}: healthy source warning absent`, await page.evaluate(()=>!document.querySelector('#main .decide-note, #main .skills-anomaly')));
  await snap(`${theme}-S3`,'Healthy fixture: no page warning or reserved warning space');
  await open('after','acm-stale','issues','.card');
  check(`${theme}: issues warning once`, await page.evaluate(()=>document.querySelectorAll('#main .decide-note').length===1 && ![...document.querySelectorAll('.card .tag')].some(n=>n.textContent==='旧数据')));
  await open('after','acm-clean','issues','.card');
  check(`${theme}: healthy issues no warning`, await page.evaluate(()=>!document.querySelector('#main .decide-note')));

  for (const side of ['before','after']) {
    await open(side,'1','configure/agents','.rt');
    const aligned = await page.evaluate(()=>({all:[...document.querySelectorAll('.rt')].every(n=>getComputedStyle(n).textAlign==='left'),x:document.querySelector('.rt').getBoundingClientRect().left}));
    if(side==='after')check(`${theme}: runtime cells left aligned`,aligned.all);
    await snap(`${theme}-S4-${side}`, `Runtime left edge x=${aligned.x.toFixed(1)}`);
  }
  await page.goto(`${config.runtimeBase}/#/configure/runtime`);
  await page.waitForSelector('.runtime-config form');
  await page.cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
  const formFit = await page.evaluate(()=>({form:document.querySelector('.runtime-config form').getBoundingClientRect().toJSON(),heading:document.querySelector('.runtime-config h2').getBoundingClientRect().toJSON()}));
  check(`${theme}: runtime form shares gutter and max-width`,formFit.form.x===formFit.heading.x && formFit.form.width<=480,JSON.stringify(formFit));
  await snap(`${theme}-S5-form`,'Isolated runtime fixture | form max-width 480px');
  await page.fill('input[aria-label="写入授权码"]','fixture authorization');
  await page.click('.runtime-config form button');
  await page.waitForSelector('.runtime-config table');
  await page.click('text="调整运行配置"');
  await page.waitForSelector('select[aria-label="模型 操作"]');
  const runtimeX = await page.evaluate(()=>document.querySelector('.runtime-config h2').getBoundingClientRect().left);
  check(`${theme}: runtime descriptions and table share gutter`,await page.evaluate(()=>{
    const x=document.querySelector('.runtime-config h2').getBoundingClientRect().left;
    return [...document.querySelectorAll('.runtime-config p,.runtime-config .table-wrap')].every(n=>n.getBoundingClientRect().left===x);
  }));
  await snap(`${theme}-S5-runtime`, `1440px | Runtime heading x=${runtimeX}`);
  await open('after','1','configure/skills','.skill-row');
  const skillsX = await page.evaluate(()=>document.querySelector('#main > h2').getBoundingClientRect().left);
  check(`${theme}: runtime and Skills share gutter`,runtimeX===skillsX,`runtime=${runtimeX}, skills=${skillsX}`);
  await page.click('#main > details.skills-disclosure:first-of-type summary');
  await snap(`${theme}-S8-sources`);
  await page.reload(); await page.waitForSelector('.skill-row');
  check(`${theme}: sources persistence`,await page.evaluate(()=>document.querySelector('#main > details.skills-disclosure').open));
  await page.click('#main > details.skills-disclosure:first-of-type summary');
  await page.click('#main > details.skills-disclosure:last-of-type summary');
  await snap(`${theme}-S8-legend`);
  await page.reload(); await page.waitForSelector('.skill-row');
  check(`${theme}: legend persistence`,await page.evaluate(()=>document.querySelector('#main > details.skills-disclosure:last-of-type').open));
  await page.click('#main > details.skills-disclosure:last-of-type summary');
  await page.click('.skills-columns summary');
  await snap(`${theme}-S9-menu`);
  for(const id of ['tri','workspace','shared','reconcile','custom','review']) await page.click(`.skills-column-options input[value="${id}"]`);
  for(const id of ['tri','workspace']) await page.click(`.skills-column-options input[value="${id}"]`);
  await snap(`${theme}-S9-hide-two`);
  await page.reload(); await page.waitForSelector('.skill-row');
  check(`${theme}: column persistence and colspan`,await page.evaluate(()=>{
    const headers=[...document.querySelectorAll('.skills-table thead th')].filter(n=>!n.hidden).length;
    return document.querySelector('[data-skill-column="tri"]').hidden && document.querySelector('[data-skill-column="workspace"]').hidden && !document.querySelector('[data-skill-column="review"]').hidden && +document.querySelector('.skill-group th').colSpan===headers;
  }));
  await page.click('.skills-columns summary');
  for(const id of ['shared','reconcile','custom','review']) await page.click(`.skills-column-options input[value="${id}"]`);
  await page.click('.skills-columns summary');
  await page.focus('.skills-columns summary'); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab');
  check(`${theme}: columns keyboard focus`, await page.evaluate(()=>document.activeElement.matches('.skills-columns summary:focus-visible') && getComputedStyle(document.activeElement).outlineStyle!=='none'));
  await snap(`${theme}-S10-columns`);
  await page.keyboard.press('Enter');
  check(`${theme}: columns keyboard expansion`,await page.evaluate(()=>document.querySelector('.skills-columns').open));
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab'); await page.keyboard.press('Tab');
  check(`${theme}: legend keyboard focus`,await page.evaluate(()=>document.activeElement.matches('#main > details.skills-disclosure:last-of-type summary:focus-visible') && getComputedStyle(document.activeElement).outlineStyle!=='none'));
  await snap(`${theme}-S10-legend`);
  await page.keyboard.press('Enter');
  check(`${theme}: legend keyboard expansion`,await page.evaluate(()=>document.querySelector('#main > details.skills-disclosure:last-of-type').open));
  await page.keyboard.press('Enter');
  check(`${theme}: targets >=24px`,await page.evaluate(()=>[...document.querySelectorAll('.tool-mark,.queue-item')].filter(n=>n.getBoundingClientRect().width).every(n=>{const r=n.getBoundingClientRect();return r.width>=24&&r.height>=24;})));
  await page.click('.skill-row .tool-mark >> nth=0');
  await page.waitForSelector('#drawer:not([hidden])');
  check(`${theme}: tool target opens drawer`,true);
  await page.keyboard.press('Escape');
  if(theme==='dark'){
    await page.goto(`${config.runtimeBase}/#/configure/runtime`);
    await page.waitForSelector('.runtime-config form');
    await page.cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
    await snap('dark-S6-form','390px | isolated runtime fixture form');
    await page.fill('input[aria-label="写入授权码"]','fixture authorization');
    await page.click('.runtime-config form button');
    await page.waitForSelector('.runtime-config table');
    await page.click('text="调整运行配置"');
    await page.waitForSelector('select[aria-label="模型 操作"]');
    await snap('dark-S6','390px | isolated runtime fixture editor');
    check('dark: narrow runtime cell padding preserved',await page.evaluate(()=>getComputedStyle(document.querySelector('.runtime-config td')).padding==='6px 4px'));
    check('dark: runtime narrow viewport fits',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
}
await fs.writeFile(`${config.out}/browser-results.json`,JSON.stringify({checks,metrics},null,2));
await task.finish({keep:[]});
if(checks.some(n=>!n.pass)) throw new Error('ACM-615 browser acceptance failed; see browser-results.json');
"""


class Handler(SimpleHTTPRequestHandler):
    roots: dict[str, Path]

    def log_message(self, *_):
        pass

    def do_GET(self):
        parts = urlsplit(self.path).path.strip('/').split('/')
        if not parts or parts[0] not in self.roots:
            self.send_error(404)
            return
        root = self.roots[parts[0]]
        relative = '/'.join(parts[1:])
        data = None
        if relative == 'fixtures/scenarios.json':
            data = json.loads((root / relative).read_text())
            data.update({'acm-clean': ['meta', 'skills'], 'acm-stale': ['meta', 'skills']})
        elif relative in ('fixtures/acm-clean/meta.json', 'fixtures/acm-stale/meta.json'):
            data = json.loads((root / 'fixtures/meta.json').read_text())
            for ws in data['workspaces']:
                ws.update(collect_state='ok', error=None)
                ws['counts'] = ws.get('counts') or {'open_issues': 0, 'agents': 0, 'runtimes': 0}
            if 'acm-stale' in relative:
                data['workspaces'][0]['collect_state'] = 'stale'
        elif relative in ('fixtures/acm-clean/skills.json', 'fixtures/acm-stale/skills.json'):
            data = json.loads((root / 'fixtures/skills.json').read_text())
            data.update(skills_schema=2, shared_root={'state': 'ok'})
            for item in data['items']:
                item['shared_entry'] = {'state': 'dir', 'label': '目录'}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            self.send_error(404)
            return
        self.directory = str(root)
        self.path = '/' + relative
        super().do_GET()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    Handler.roots = {'before': args.baseline.resolve(), 'after': Path(__file__).resolve().parent}
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime_process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / 'backend/tests/runtime_write_fixture_server.py'), '--port', '0'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not select.select([runtime_process.stdout], [], [], 10)[0]:
            raise RuntimeError('Runtime fixture did not become ready')
        ready = runtime_process.stdout.readline().strip()
        if not ready.startswith('Fixture ready '):
            raise RuntimeError(ready)
        runtime_base = ready.split(' ')[2].split('/#/')[0]
        config = {'base': f'http://127.0.0.1:{server.server_port}', 'runtimeBase': runtime_base, 'out': str(out)}
        result = subprocess.run(['ego-browser', 'nodejs'], input='const config = '+json.dumps(config)+';\n'+SCRIPT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / 'browser.log').write_text(result.stdout)
        print(result.stdout)
    finally:
        runtime_process.terminate()
        runtime_process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join()
    if result.returncode:
        raise SystemExit(result.returncode)
    report = json.loads((out / 'browser-results.json').read_text())
    for theme in ('light', 'dark'):
        before = Image.open(out / f'{theme}-topbar-before.png')
        after = Image.open(out / f'{theme}-topbar-after.png')
        same = before.size == after.size and ImageChops.difference(before.convert('RGB'), after.convert('RGB')).getbbox() is None
        report['checks'].append({'label': f'{theme}: topbar pixel identical', 'pass': same})
        print(f'{"PASS" if same else "FAIL"} {theme}: topbar pixel identical')
        for label, left, right in [('S2', 'S1-after', 'S2-skills'), ('S5', 'S5-runtime', 'S7-after')]:
            a, b = [Image.open(out / f'{theme}-{name}.png').convert('RGB') for name in (left, right)]
            combined = Image.new('RGB', (a.width + b.width, max(a.height, b.height)))
            combined.paste(a, (0, 0))
            combined.paste(b, (a.width, 0))
            combined.save(out / f'{theme}-{label}-comparison.png')
    (out / 'browser-results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    total = len(report['checks'])
    passed = sum(c['pass'] for c in report['checks'])
    print(f'{passed}/{total} checks passed')
    if passed != total:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
