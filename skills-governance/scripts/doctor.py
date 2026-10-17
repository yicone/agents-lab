#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from broken_links import repair,scan
PACKAGE_ROOT=Path(__file__).resolve().parents[1]; DEFAULT_INVENTORY=PACKAGE_ROOT/'config/skills-inventory.json'; DEFAULT_REPO_ROOT=PACKAGE_ROOT.parent
def _path(v,r):
 c=Path(v).expanduser(); return c if c.is_absolute() else r/c
def inspect(inv,root,do_repair=False):
 adapters={}; roots=set(); out=[]
 for s in inv.get('skills',[]):
  for a in s.get('adapters',[]):
   loc=a.get('path')
   if not isinstance(loc,str): continue
   p=_path(loc,root); adapters[p]=(s.get('name'),a); roots.add(p if p.is_dir() else p.parent)
   if a.get('kind')=='declared_target' and isinstance(a.get('target'),str) and not _path(a['target'],root).exists(): out.append({'kind':'declared_target_missing','skill':s.get('name'),'path':str(p),'target':a['target'],'repaired':False})
 for p in sorted({x for r in roots for x in scan(r)}):
  s,a=adapters.get(p,(None,None)); target=a.get('target') if a else None; item={'kind':'broken_symlink' if a else 'orphan_broken_symlink','skill':s,'path':str(p),'target':target,'repaired':False}
  if do_repair and a and a.get('kind')=='symlink' and isinstance(target,str):
   q=Path(target).expanduser(); q=q if q.is_absolute() else p.parent/q
   if q.exists(): repair(p,target); item['repaired']=True
  out.append(item)
 return out
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--inventory',type=Path,default=DEFAULT_INVENTORY); p.add_argument('--repo-root',type=Path,default=DEFAULT_REPO_ROOT); p.add_argument('--repair',action='store_true'); p.add_argument('--format',choices=('json','text'),default='json'); a=p.parse_args(argv)
 f=inspect(json.loads(a.inventory.expanduser().read_text()),a.repo_root.expanduser().resolve(),a.repair); rem=[x for x in f if not x['repaired']]
 if a.format=='text': print('No adapter link problems found.' if not f else '\n'.join(f"{x['kind']}: {x['path']} -> {x.get('target')}"+(' [repaired]' if x['repaired'] else '') for x in f))
 else: print(json.dumps({'broken':f,'count':len(rem),'repaired':sum(x['repaired'] for x in f)},indent=2))
 return 1 if rem else 0
if __name__=='__main__': sys.exit(main())
