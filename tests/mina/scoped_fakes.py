"""実DriveClientの外部HTTPだけをメモリへ置換する隔離用transport。"""
import hashlib
import json
from src.mina.drive import DriveClient


class SyntheticDrive(DriveClient):
    def __init__(self):
        super().__init__('synthetic','synthetic','synthetic','synthetic_folder_123')
        self.files={};self.calls=[];self.stats={'writes':0}

    def seed(self,key,raw,name):self.files[key]=(raw,name,{})
    def _authorization(self):return {}

    def _transport_request(self,url,data=None,headers=None,method='GET',limit=1048576,write=False):
        self.calls.append({'method':method,'url':url})
        if 'upload/drive' in url:
            self.stats['writes']+=1;key='synthetic_new_id_'+str(self.stats['writes'])
            meta=json.loads(data.split(b'\r\n\r\n',1)[1].split(b'\r\n--',1)[0]);boundary=headers['Content-Type'].split('boundary=')[1].encode()
            raw=data.split(b'Content-Type: image/jpeg\r\n\r\n',1)[1].rsplit(b'\r\n--'+boundary+b'--\r\n',1)[0]
            self.files[key]=(raw,meta['name'],meta['appProperties']);return json.dumps({'id':key}).encode()
        fid=url.split('/files/')[1].split('?')[0]
        if fid.endswith('/permissions'):
            assert method=='POST' and json.loads(data)=={'type':'anyone','role':'reader','allowFileDiscovery':False}
            assert fid.removesuffix('/permissions') in self.files
            return b'{}'
        if fid==self.folder_id:return json.dumps({'id':fid,'mimeType':'application/vnd.google-apps.folder','trashed':False,'capabilities':{'canAddChildren':True}}).encode()
        raw,name,props=self.files[fid]
        if 'alt=media' in url:return raw
        return json.dumps({'id':fid,'name':name,'mimeType':'image/jpeg','parents':[self.folder_id],'trashed':False,'size':str(len(raw)),
            'md5Checksum':hashlib.md5(raw).hexdigest(),'appProperties':props}).encode()
