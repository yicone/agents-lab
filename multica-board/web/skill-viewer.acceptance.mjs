/** Start backend/tests/skill_viewer_fixture_server.py, then call run(page, outputDir)
 * in ego-browser nodejs. outputDir must be absolute. No live account data is used.
 */
export async function run(page, outputDir) {
  const results = [];
  const check = (name, pass) => { results.push({name,pass:!!pass}); if (!pass) throw Error(`FAIL: ${name}`); };
  await page.goto('about:blank');
  await page.goto('http://127.0.0.1:8797/#skills');
  await page.waitForSelector('.skill-row');
  check('dynamic providers and complete legend', await page.evaluate(() => document.querySelector('.skills-table').textContent.includes('future-runtime') && document.querySelector('.skills-table').textContent.includes('unknown') && document.querySelector('.skills-legend').textContent.includes('空白：')));
  await page.screenshot({path:`${outputDir}/skills-runtime.png`});
  await page.click('.skill-row');
  await page.click('loc=css:.skill-content > p > button >> nth=0');
  await page.waitForSelector('.skill-markdown h1');
  check('Markdown/frontmatter and inert source HTML', await page.evaluate(() => document.querySelector('.skill-markdown h1').textContent === 'Viewer demo' && document.querySelector('.skill-markdown table') && !document.querySelector('.skill-markdown script, .skill-markdown a[href^="javascript:"]') && !window.viewerInjected && document.querySelector('#drawer').textContent.includes('A safe viewer fixture')));
  await page.click('loc=css:.skill-content details summary >> nth=0');
  check('raw source is preserved', await page.evaluate(() => document.querySelector('.skill-body').textContent.startsWith('---\nname: viewer-demo')));
  await page.click('loc=css:.skill-content details summary >> nth=0');
  await page.click('loc=css:.skill-content > p > button >> nth=1');
  await page.waitForSelector('.skill-comparisons summary');
  await page.click('.skill-comparisons summary');
  check('two-source read-only diff', await page.evaluate(() => document.querySelector('.skill-comparisons .diff-add').textContent.includes('changed') && document.querySelector('.skill-comparisons .diff-del').textContent.includes('original')));
  await page.click('loc=css:.skill-files summary >> nth=0');
  await page.click('loc=css:.skill-files button >> nth=0');
  await page.waitForSelector('.skill-file-tree');
  await page.click('loc=css:.skill-file-tree button:text-is("references/note.md")');
  await page.waitForFunction(() => [...document.querySelectorAll('.skill-markdown h1')].some(n=>n.textContent==='Reference note'));
  check('relative file preview retains expanded tree', await page.evaluate(() => document.querySelector('.skill-files').open));
  await page.click('loc=css:.skill-file-tree button:text-is("assets/image.bin")');
  await page.waitForFunction(() => document.querySelector('.skill-file-tree').textContent.includes('二进制或非 UTF-8'));
  check('binary bytes never rendered', await page.evaluate(() => ![...document.querySelectorAll('.skill-body')].some(n=>n.textContent.includes('\u0000'))));
  await page.click('loc=css:.skill-file-tree button:text-is("empty.txt")');
  await page.waitForFunction(() => document.querySelector('.skill-file-tree').textContent.includes('文件为空'));
  check('cloud remains explicitly uncollected', await page.evaluate(() => document.querySelector('#drawer').textContent.includes('云端正文未采集')));
  await page.screenshot({path:`${outputDir}/skills-files.png`});
  await page.click('.skill-comparisons summary');
  await page.click('.skill-comparisons summary');
  await page.screenshot({path:`${outputDir}/skills-diff.png`});
  await page.click('button[aria-label="关闭"]');
  await page.goto('http://127.0.0.1:8797/#/skills?tool=future-runtime');
  await page.waitForFunction(() => location.hash.includes('tool=future-runtime') && document.querySelectorAll('.skill-row').length===0);
  check('dynamic tool filter survives reload', await page.evaluate(() => location.hash.includes('tool=future-runtime')));
  await page.goto('about:blank');
  await page.goto('http://127.0.0.1:8797/#/skills?tool=future-runtime');
  await page.waitForFunction(() => document.querySelector('.skills-table')?.textContent.includes('future-runtime'));
  check('future provider deep link survives a full page load', await page.evaluate(() => location.hash.includes('tool=future-runtime') && document.querySelectorAll('.skill-row').length === 0));
  return results;
}


/** Isolated retry regression: start without loading SKILL.md. */
export async function runRetry(page, outputDir) {
  const results = [];
  const check = (name, pass) => { results.push({name,pass:!!pass}); if (!pass) throw Error(`FAIL: ${name}`); };
  await page.goto('about:blank');
  await page.goto('http://127.0.0.1:8797/#skills');
  await page.waitForSelector('#skill-viewer-demo');
  await page.click('#skill-viewer-demo');
  await page.evaluate(() => {
    window.retryRequests = [];
    const original = window.fetch;
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (String(args[0]).startsWith('/api/skills/content')) {
        const body = await response.clone().json();
        window.retryRequests.push({url:String(args[0]),state:body.state,hasResponseFile:Object.hasOwn(body,'file')});
      }
      return response;
    };
  });
  await page.click('loc=css:.skill-files summary >> nth=0');
  await page.click('loc=css:.skill-files button >> nth=0');
  await page.waitForSelector('.skill-file-tree');
  await page.click('loc=css:.skill-file-tree button:text-is("assets/image.bin")');
  await page.waitForFunction(() => window.retryRequests.some(r=>r.state==='unsupported'));
  await page.click('loc=css:.skill-file-tree button:text-is("重试")');
  await page.waitForFunction(() => window.retryRequests.filter(r=>r.state==='unsupported').length===2);
  check('binary retry retains assets/image.bin without response.file', await page.evaluate(() => {
    const attempts=window.retryRequests.filter(r=>r.state==='unsupported');
    return attempts.length===2 && attempts.every(r=>new URL(r.url,location.origin).searchParams.get('file')==='assets/image.bin' && !r.hasResponseFile);
  }));
  check('binary retry never loads main document or Viewer demo', await page.evaluate(() => !document.querySelector('.skill-file-tree').textContent.includes('Viewer demo') && !document.querySelector('.skill-markdown') && window.retryRequests.every(r=>new URL(r.url,location.origin).searchParams.has('file') || new URL(r.url,location.origin).searchParams.has('tree'))));
  await page.screenshot({path:`${outputDir}/skills-retry-binary.png`});
  await page.click('loc=css:.skill-file-tree button:text-is("references/retry-once.txt")');
  await page.waitForFunction(() => window.retryRequests.some(r=>r.state==='error'));
  await page.click('xpath=//ul[contains(@class,"skill-file-tree")]/li[button[normalize-space()="references/retry-once.txt"]]//button[normalize-space()="重试"]');
  await page.waitForFunction(() => document.querySelector('.skill-file-tree').textContent.includes('Attachment retry recovered'));
  check('text failure retry recovers same relative file', await page.evaluate(() => {
    const attempts=window.retryRequests.filter(r=>new URL(r.url,location.origin).searchParams.get('file')==='references/retry-once.txt');
    return attempts.length===2 && attempts[0].state==='error' && !attempts[0].hasResponseFile && attempts[1].state==='ok' && attempts[0].url===attempts[1].url;
  }));
  check('text retry leaves main document unloaded', await page.evaluate(() => !document.querySelector('.skill-file-tree').textContent.includes('Viewer demo') && !document.querySelector('.skill-markdown')));
  await page.screenshot({path:`${outputDir}/skills-retry-text.png`});
  return {results,requests:await page.evaluate(()=>window.retryRequests)};
}
