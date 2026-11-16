import json
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QNetworkReply
from online_updates import ReleaseCheck
from update_worker import package_url
app=QApplication.instance() or QApplication([])
class Reply:
 def __init__(self,data,error=False):self.data=data;self.failed=error
 def error(self):return QNetworkReply.NetworkError.ContentNotFoundError if self.failed else QNetworkReply.NetworkError.NoError
 def attribute(self,*args):return 404 if self.failed else 200
 def readAll(self):return json.dumps(self.data).encode()
 def deleteLater(self):pass
c=ReleaseCheck((1,77,0),app)
asset={'name':'PDF-Werkbank-1.77.0-Windows.zip','state':'uploaded','browser_download_url':package_url('1.77.0'),'digest':'sha256:'+'a'*64,'size':10}
data={'tag_name':'v1.77.0','assets':[asset],'body':'<img src="https://invalid"> literal notes'}
def complete(value,error=False):
 c.pending=Reply(value,error);c.attempt=2;c.complete()
complete(data);assert c.latest_version=='1.77.0' and c.release_notes==data['body']
complete(dict(data,body='x'*30000));assert len(c.release_notes)==24000
for changed in ({'draft':True},{'prerelease':True},{'tag_name':'bad'},{'assets':[]}):
 complete(dict(data,**changed));assert c.latest_version is None and c.release_notes==''
complete(dict(data,body=None));assert c.latest_version=='1.77.0' and c.release_notes==''
complete(data);complete({},True);assert c.latest_version is None and c.release_notes==''
complete(data);complete({'tag_name':True});assert c.latest_version is None
print('PASS release metadata: current version, no-update notes, drafts/prereleases invalid assets, missing/long/plain-text notes and failed check reset.')
