import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts')); import doctor
class BrokenLinksTests(unittest.TestCase):
 def inv(self,r,a):
  p=r/'inventory.json'; p.write_text(json.dumps({'skills':[{'name':'one','adapters':a}]})); return p
 def test_relative_target_repair(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); (r/'skills/one').mkdir(parents=True); (r/'adapters').mkdir(); (r/'adapters/one').symlink_to('../skills/one'); d=json.loads(self.inv(r,[{'path':'adapters/one','kind':'symlink','target':'../skills/one'}]).read_text()); self.assertEqual(doctor.inspect(d,r,True),[])
 def test_unrepairable_orphan_fail(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); (r/'adapters').mkdir(); (r/'adapters/known').symlink_to('missing'); (r/'adapters/orphan').symlink_to('gone'); self.inv(r,[{'path':'adapters/known','kind':'symlink','target':'missing'}]); self.assertEqual(doctor.main(['--inventory',str(r/'inventory.json'),'--repo-root',str(r),'--repair']),1)
 def test_declared_target(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); (r/'target').mkdir(); d=json.loads(self.inv(r,[{'path':'target','kind':'declared_target','target':'missing'}]).read_text()); self.assertEqual(doctor.inspect(d,r),[{'kind':'declared_target_missing','skill':'one','path':str(r/'target'),'target':'missing','repaired':False}])
if __name__=='__main__': unittest.main()
