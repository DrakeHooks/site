"""Additional isolated tests: notes, video, filename collision, and duplicates."""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def cmd(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, stderr=subprocess.STDOUT, text=True).strip()


def main():
    with tempfile.TemporaryDirectory(prefix="drake-v2-extra-") as tmp:
        root = Path(tmp)
        remote, site, data = root/'remote.git', root/'site', root/'data'
        cmd('git','init','--bare','-b','main',str(remote))
        cmd('git','clone',str(remote),str(site))
        cmd('git','config','user.name','Test',cwd=site)
        cmd('git','config','user.email','test@example.com',cwd=site)
        (site/'README.md').write_text('test\n')
        cmd('git','add','.',cwd=site)
        cmd('git','commit','-m','init',cwd=site)
        cmd('git','push','origin','main',cwd=site)
        os.environ['DATA_DIR']=str(data)
        os.environ['SITE_DIR']=str(site)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
        import common,worker
        common.setup_dirs()
        def makejob(jid, files, note='', collection='archive', day='2026-11-21'):
            path=common.JOBS/jid
            incoming=path/'incoming';incoming.mkdir(parents=True)
            entries=[]
            for name, contents in files:
                p=incoming/name;p.write_bytes(contents)
                entries.append({'stored':name,'name':name,'sha256':hashlib.sha256(contents).hexdigest(),'bytes':len(contents)})
            manifest={'id':jid,'collection':collection,'day':day,'no_date':False,
                      'note':note,'allow_duplicates':False,'files':entries}
            common.atomic_json(path/'manifest.json',manifest)
            common.set_status(jid,id=jid,state='queued',created_at=common.now().isoformat())
        jpg=root/'one.jpg'
        cmd('ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=green:s=64x64:d=1',
            '-frames:v','1',str(jpg))
        photo=jpg.read_bytes()
        with worker.registry() as db:
            makejob('a'*32,[('scene.jpg',photo)],'hello birds')
            worker.process('a'*32,db)
            assert common.get_status('a'*32)['state']=='published'
            makejob('b'*32,[('scene.jpg',photo)],'')
            worker.process('b'*32,db)
            assert common.get_status('b'*32)['published_count']==0
            assert not (common.JOBS/('b'*32)/'incoming').exists()
            makejob('c'*32,[],'note only')
            worker.process('c'*32,db)
            md=(site/'content/archive/2026-11-21/index.md').read_text()
            assert 'hello birds' in md and 'note only' in md
            assert md.count('figure src="scene.avif"')==1
            mov=root/'clip.mov'
            cmd('ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=96x64:r=12:d=1',
                '-c:v','mpeg4',str(mov))
            makejob('d'*32,[('clip.mov',mov.read_bytes())],'',collection='birds')
            worker.process('d'*32,db)
            assert (site/'content/birds/2026-11-21/clip.mp4').exists()
            assert '<video controls' in (site/'content/birds/2026-11-21/index.md').read_text()
            assert not (common.JOBS/('d'*32)/'incoming').exists()
        print('PASS: duplicate skip, note-only append, and MOV→MP4 with no retained originals')


if __name__=='__main__':main()
