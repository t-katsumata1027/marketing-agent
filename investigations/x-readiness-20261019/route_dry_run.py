"""本番台帳のメモリ内コピーでBuffer経路だけを組み立て、実送信しない。"""
import json,sqlite3,sys
from contextlib import contextmanager
sys.path.insert(0,'/opt/marketing-agent')
from src.mina.buffer_publisher import publication_client,BufferPublisher
source=sqlite3.connect('file:/var/lib/mina-agent/mina.sqlite3?mode=ro',uri=True);source.execute('PRAGMA query_only=ON')
memory=sqlite3.connect(':memory:');memory.row_factory=sqlite3.Row;source.backup(memory);source.close()
class MemoryStore:
 @contextmanager
 def transaction(self):
  try:
   memory.execute('BEGIN IMMEDIATE');yield memory;memory.commit()
  except BaseException:memory.rollback();raise
rows=[dict(r) for r in memory.execute("SELECT * FROM candidates WHERE target_date='2026-10-19' ORDER BY scheduled")]
out={'memory_copy_only':True,'production_route_writes':0,'http_requests':0,'slots':[]}
for row in rows:
 publisher=publication_client(MemoryStore(),row,object())
 out['slots'].append({'slot':row['slot'],'uses_buffer':isinstance(publisher,BufferPublisher),'route_revision_matches':publisher.route['revision']==row['revision'],'route_digest_matches':publisher.route['digest']==row['digest'],'route_state':publisher.route['state']})
out['all_four_ready']=len(rows)==4 and all(e['uses_buffer'] and e['route_revision_matches'] and e['route_digest_matches'] and e['route_state']=='armed' for e in out['slots'])
memory.close();print(json.dumps(out,ensure_ascii=False,indent=2))
