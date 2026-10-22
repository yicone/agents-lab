"""Local UI fixture, no credentials. Run: python3 tests/skill_viewer_fixture_server.py [port]."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.server import BoardServer
from multica_board.skill_content import read_content
from multica_board.skill_runtimes import runtime_columns
WEB = Path(__file__).resolve().parents[2] / 'web'
REPO = WEB.parents[1]
def fixture(name):
    path = WEB / 'fixtures' / f'{name}.json'
    return json.loads(path.read_text()) if path.exists() else {'items': []}
with tempfile.TemporaryDirectory(prefix='atlas-viewer-') as folder:
    root = Path(folder)
    for source, word in [('shared','original'), ('direct','changed')]:
        skill = root / source / 'viewer-demo'
        for sub in ['references','scripts','assets']: (skill/sub).mkdir(parents=True,exist_ok=True)
        (skill/'SKILL.md').write_text(f'---\nname: viewer-demo\ndescription: |\n  A safe viewer fixture\n  with multiline fields\nlabels: [read-only, demo]\n---\n# Viewer demo\n\nThis is **{word}** content.\n\n| File | Kind |\n|---|---|\n| SKILL.md | Markdown |\n\n- Text preview\n- Read-only diff\n\n```js\nconsole.log("demo");\n```\n\n<script>window.viewerInjected = true</script>\n\n[Unsafe](javascript:alert(1))\n')
        (skill/'references'/'retry-once.txt').write_text('Attachment retry recovered\n')
        (skill/'references'/'note.md').write_text('# Reference note\n\nReference **text**.\n')
        (skill/'scripts'/'demo.py').write_text('print("read only")\n')
        (skill/'assets'/'image.bin').write_bytes(b'\x00\xffbinary')
        (skill/'empty.txt').touch()
    shared, direct = [str(root/source/'viewer-demo') for source in ['shared','direct']]
    base = fixture('skills')
    item = dict(base['items'][0], name='viewer-demo', source_class='local_custom', source_label='验收 fixture',local_source={'path':shared},entries=[{'path':shared,'present':True,'scope':'user'},{'path':direct,'present':True,'scope':'user'}],tools={'codex':'shared','claude':'direct'},evidence=[],workspace_installs=[{'workspace_id':'fixture','workspace_name':'Fixture cloud','id':'cloud-demo','agents':[]}],shared_entry={'state':'dir','path':shared,'reason':'present'},reconciliation_status='aligned')
    item.update(scope='user',channel_label='验收 fixture',channel_detail='本机合成数据',status_label='一致',current_version='1',observed_version='1',findings=[],ownership='local',update_authority='local',source_authority='fixture')
    base.update(skills_schema=2,items=[item],shared_root={'path':str(root/'shared'),'state':'ok'},attention={'badge':0,'baseline_debt':0,'counts':{},'buckets':{}},summary=[{'id':'local_custom','label':'验收 fixture','count':1,'statuses':{}}])
    config=json.loads((REPO/'skills-governance/config/tool-roots.json').read_text()); base['tools']=config['tools']
    runtimes={'items':[{'provider':p,'id':p,'workspace_id':'fixture'} for p in ['codex','antigravity','mcode','omp','future-runtime',None]]}
    class FixtureViews:
        def skills(self,*_):
            base['generated_at']=datetime.now(timezone.utc).isoformat()
            return runtime_columns(base,runtimes,config['roots'])
        content_attempts = set()
        def skills_content(self,**args):
            key = (args.get('path'), args.get('file'))
            if args.get('file') == 'references/retry-once.txt' and key not in self.content_attempts:
                self.content_attempts.add(key)
                # Deliberately omit file: retries must retain request context.
                return {'ok':False,'state':'error','message':'Fixture: first text read failed'}
            return read_content(base,repo_root=REPO,**args)
        def skills_meta(self,*_): return self.skills()
        def skills_attention(self,*_): return base['attention']
        def meta(self): return fixture('meta')
        def issues(self): return fixture('issues')
        def agents(self): return fixture('agents')
        def runtimes(self): return fixture('runtimes')
        def bots(self): return fixture('bots')
        def usage(self): return fixture('usage')
        def layers(self): return fixture('layers')
        def changes(self,*_): return fixture('changes')
    server=BoardServer('127.0.0.1',int(sys.argv[1]) if len(sys.argv)>1 else 8797,FixtureViews(),static_dir=WEB)
    print(f'Fixture ready http://127.0.0.1:{server.server_port}/#skills',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
