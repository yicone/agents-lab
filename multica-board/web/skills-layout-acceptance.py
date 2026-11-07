"""Check compact Skills widths with 7/15 tools and optional real read-only APIs.

python3 multica-board/web/skills-layout-acceptance.py --baseline /old/web --out /artifacts --live 8765 8767
Serves candidate assets locally; GET responses from each live port are cached in
memory and shared by its baseline/candidate pair. Never changes deployed files,
preferences on the live origin, services, or configuration. Requires ego-browser.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen


TOOLS = [
    ("cursor", "Cursor"), ("codex", "Codex"), ("claude", "Claude Code"),
    ("hermes", "Hermes Agent"), ("gemini", "Gemini CLI"), ("grok", "Grok CLI"),
    ("copilot", "GitHub Copilot CLI"), ("opencode", "OpenCode"),
    ("antigravity", "Antigravity"), ("mcode", "Devin CLI"), ("omp", "Oh My Pi"),
    ("factory", "Factory Droid"), ("amp", "Amp"), ("devin", "devin"),
    ("project", "仓库入口"),
]


class Source:
    def __init__(self, web, tools, port=None):
        self.web, self.tools, self.port = web, tools, port
        self.cache, self.provenance = {}, {}
        self.lock = threading.Lock()
        self.posts = 0

    def get(self, path):
        with self.lock:
            if path in self.cache:
                return self.cache[path]
            if self.port:
                try:
                    with urlopen(f'http://127.0.0.1:{self.port}{path}', timeout=60) as response:
                        status, body = response.status, response.read()
                except HTTPError as error:
                    status, body = error.code, error.read()
            else:
                name = urlsplit(path).path.removeprefix('/api/')
                file = self.web / 'fixtures' / (name + '.json')
                payload = json.loads(file.read_text()) if file.is_file() else {'items': []}
                if name == 'skills':
                    selected = TOOLS if self.tools == 15 else TOOLS[:6] + [TOOLS[-1]]
                    payload['tools'] = [{'id': key, 'label': label} for key, label in selected]
                status, body = 200, json.dumps(payload).encode()
            self.cache[path] = status, body
            record = {'status': status, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            if urlsplit(path).path == '/api/skills' and status == 200:
                payload = json.loads(body)
                record.update(tool_count=len(payload.get('tools', [])), item_count=len(payload.get('items', [])))
            self.provenance[path] = record
            return status, body


def handler(web, source):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web), **kwargs)

        def log_message(self, *_):
            pass

        def do_POST(self):
            source.posts += 1
            self.send_error(405, 'Read-only layout verification')

        def do_GET(self):
            if self.path.startswith('/api/'):
                status, body = source.get(self.path)
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()
    return Handler


SCRIPT = r"""
const fs = await import('node:fs/promises');
const task = await taskSpace('ACM-615 live layout fix');
const p = task.page('p1');
const records = [], checks = [];
function check(label, pass) { checks.push({label,pass:!!pass}); console.log(`${pass?'PASS':'FAIL'} ${label}`); }
async function open(url) {
  await p.goto(`${url}/#/configure/skills`);
  await p.waitForSelector('.skill-row',{timeout:60000});
  await p.cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
}
async function measure() {
  return await p.evaluate(()=>{
    const table=document.querySelector('.skills-main .skills-table'), wrap=table.parentElement;
    const rows=[...table.querySelectorAll('.skill-row')];
    let overlap=0;
    for(const row of rows.slice(0,20))for(const cell of row.cells){
      if(cell.hidden)continue;
      const bounds=cell.getBoundingClientRect(),walker=document.createTreeWalker(cell,NodeFilter.SHOW_TEXT);
      let text;
      while(text=walker.nextNode()){
        if(!text.textContent.trim())continue;
        let ancestor=text.parentElement,clipped=false;
        while(ancestor&&ancestor!==cell){if(['hidden','clip'].includes(getComputedStyle(ancestor).overflowX)){clipped=true;break;}ancestor=ancestor.parentElement;}
        if(clipped)continue;
        const range=document.createRange();range.selectNodeContents(text);
        for(const r of range.getClientRects())if(r.width>0&&(r.right>bounds.right+1||r.left<bounds.left-1))overlap++;
      }
    }
    return {
      toolCount:table.querySelectorAll('thead .tool-col').length,
      nameWidth:table.querySelector('thead th').getBoundingClientRect().width,
      firstHeight:rows[0].getBoundingClientRect().height,
      firstY:rows[0].getBoundingClientRect().top,
      visibleRows:rows.filter(n=>{const r=n.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}).length,
      width:wrap.clientWidth,scrollWidth:wrap.scrollWidth,overlap,
      queueWidth:document.querySelector('.skills-queue').getBoundingClientRect().width,
      minToolWidth:Math.min(...[...rows[0].querySelectorAll('.tool-mark')].map(n=>n.getBoundingClientRect().width)),
      sticky:getComputedStyle(table.querySelector('thead th')).position,
      rowPadding:getComputedStyle(rows[0].cells[0]).padding,
      headerHeight:table.querySelector('thead').getBoundingClientRect().height,
    };
  });
}
async function screenshot(name, m) {
  await p.evaluate(text=>{const n=document.createElement('div');n.id='layout-measurement';n.textContent=text;Object.assign(n.style,{position:'fixed',bottom:'0',left:'0',zIndex:9999,padding:'6px 10px',color:'var(--text)',background:'var(--panel)',border:'1px solid var(--line)',font:'12px monospace'});document.body.append(n);},`1440x900 | tools=${m.toolCount} | name=${m.nameWidth.toFixed(1)}px | row=${m.firstHeight.toFixed(1)}px | visible=${m.visibleRows} | ${m.width}/${m.scrollWidth}px`);
  await p.screenshot({path:`${config.out}/${name}.png`});
  await p.evaluate(()=>document.querySelector('#layout-measurement').remove());
}
for(const entry of config.entries){
  for(const theme of ['light','dark']){
    await p.cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:theme}]});
    // Direct live pages are observed only; all interactive tests use a local proxy origin.
    if(entry.live){await open(entry.live);const m=await measure();records.push({source:entry.name,theme,side:'live',...m});await screenshot(`${entry.name}-${theme}-live`,m);}
    await open(entry.before);const before=await measure();await screenshot(`${entry.name}-${theme}-before`,before);
    await open(entry.after);const after=await measure();await screenshot(`${entry.name}-${theme}-after`,after);
    records.push({source:entry.name,theme,side:'before',...before},{source:entry.name,theme,side:'after',...after});
    console.log(JSON.stringify({source:entry.name,theme,before,after}));
    const label=`${entry.name}/${theme}`;
    check(`${label}: full tool set`,after.toolCount===entry.tools);
    check(`${label}: name width >=204px`,after.nameWidth>=203.9);
    check(`${label}: no horizontal overflow at 1440`,after.scrollWidth<=after.width);
    check(`${label}: no text crosses adjacent cells`,after.overlap===0);
    check(`${label}: row density restored`,after.firstHeight<100&&after.visibleRows>=4);
    check(`${label}: header remains compact`,after.headerHeight<80);
    check(`${label}: targets and previous layout`,after.minToolWidth>=24&&after.queueWidth===352&&after.sticky==='sticky'&&after.rowPadding==='6px 8px');
    await p.click('.skills-columns summary');await p.click('.skills-column-options input[value="tri"]');
    await p.reload();await p.waitForSelector('.skill-row');
    check(`${label}: column persistence`,await p.evaluate(()=>!document.querySelector('[data-skill-column="tri"]').hidden));
    await p.click('.skills-columns summary');await p.click('.skills-column-options input[value="tri"]');await p.click('.skills-columns summary');
    check(`${label}: default layout recovers after column toggle`,(await measure()).nameWidth>=203.9);
    await p.click('.skill-row .tool-mark >> nth=0');await p.waitForSelector('#drawer:not([hidden])');await p.keyboard.press('Escape');
    check(`${label}: drawer still opens`,true);
  }
}
await fs.writeFile(`${config.out}/browser-results.json`,JSON.stringify({records,checks},null,2));
await task.finish({keep:[]});
console.log(`${checks.filter(c=>c.pass).length}/${checks.length} checks passed`);
if(checks.some(c=>!c.pass))throw new Error('Skills layout acceptance failed');
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--live', nargs='*', type=int, default=[])
    args = parser.parse_args()
    web = Path(__file__).resolve().parent
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    servers, threads, entries, sources = [], [], [], []
    try:
        for name, source, live in [('fixture-7', Source(web, 7), None), ('fixture-15', Source(web, 15), None)] + [
            (f'live-{port}', Source(web, 15, port), f'http://127.0.0.1:{port}') for port in args.live
        ]:
            entry = {'name': name, 'tools': source.tools, 'live': live}
            for side, directory in [('before', args.baseline.resolve()), ('after', web)]:
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler(directory, source))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                servers.append(server)
                threads.append(thread)
                entry[side] = f'http://127.0.0.1:{server.server_port}'
            entries.append(entry)
            sources.append((name, source))
        config = {'entries': entries, 'out': str(out)}
        process = subprocess.Popen(['ego-browser', 'nodejs'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        process.stdin.write('const config='+json.dumps(config)+';\n'+SCRIPT)
        process.stdin.close()
        with (out / 'browser.log').open('w') as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end='', flush=True)
        process.wait()
        (out / 'source-provenance.json').write_text(json.dumps({name: {'port': source.port, 'responses': source.provenance,
            'post_attempts': source.posts} for name, source in sources}, indent=2))
        assert all(source.posts == 0 for _, source in sources), 'Unexpected POST attempt'
        if process.returncode:
            raise SystemExit(process.returncode)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


if __name__ == '__main__':
    main()
