import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import session

MON={'name':'A','id':0,'x':0,'y':0,'width':1920,'height':1080,'scale':1,'focused':True,'activeWorkspace':{'id':1,'name':'1'}}
def window(addr='0x1'):
    return {'address':addr,'pid':123,'class':'foot','initialClass':'foot','title':'test','workspace':{'id':1,'name':'1'},'floating':True,'at':[100,100],'size':[800,600],'monitor':'A','launch':{'kind':'terminal','program':'foot'},'fullscreen':0,'fullscreenClient':0,'pinned':False}
def snapshot():return {'format':1,'saved_at':'now','session':'test','windows':[window()],'monitors':[MON],'active':'0x1','workspaces':[]}

class Tests(unittest.TestCase):
    def test_identity_prefers_unique_running_executable(self):
        class App:
            def __init__(self,id,executable):self.id=id;self.executable=executable
            def get_id(self):return self.id
            def get_executable(self):return self.executable
            def get_startup_wm_class(self):return 'Hermes'
        wrapper=App('hermes.desktop','/usr/bin/mise')
        binary=App('hermes-desktop.desktop','/opt/Hermes')
        with patch('session.process_executable',return_value='Hermes'):
            result=session.identity({'pid':123,'initialClass':'Hermes'},[wrapper,binary])
        self.assertEqual(result,{'kind':'desktop','id':'hermes-desktop.desktop'})

    def test_shutdown_does_not_erase(self):
        old=snapshot();guard=session.Stabilizer(old);empty=copy.deepcopy(old);empty['windows']=[]
        self.assertIsNone(guard.observe(empty,0));self.assertIsNone(guard.observe(empty,2));self.assertIsNone(guard.observe(empty,19))
        self.assertEqual(guard.saved,old)
        self.assertEqual(guard.observe(empty,20)['windows'],[])
    def test_stable_move_saved(self):
        old=snapshot();guard=session.Stabilizer(old);new=copy.deepcopy(old);new['windows'][0]['at']=[300,200]
        self.assertIsNone(guard.observe(new,0));self.assertIsNotNone(guard.observe(new,3))
    def test_title_animation_does_not_prevent_saving(self):
        a=snapshot();b=copy.deepcopy(a);b['windows'][0]['title']='spinner'
        self.assertEqual(session.fingerprint(a),session.fingerprint(b))
    def test_lua_quote(self):
        self.assertEqual(session.lua('"; bad()\n'), '"\\"; bad()\\n"')
        self.assertEqual(session.lua({'follow':False}),'{follow=false}')
    def test_monitor_fallback_clamps(self):
        w=window();w['at']=[9999,-400]
        new={**MON,'name':'B','width':1280,'height':720}
        target,x,y,width,height=session.geometry(w,[MON],[new])
        self.assertEqual(target['name'],'B');self.assertGreaterEqual(x,0);self.assertEqual(y,0);self.assertLessEqual(x+width,1280)
    def test_terminal_never_replays_job(self):
        w=window();w['cwd']='/tmp';w['cmdline']=['rm','-rf','anything']
        self.assertEqual(session.terminal_command(w),['foot','--working-directory=/tmp'])
    def test_terminal_attaches_saved_tmux_session(self):
        w=window();w['cwd']='/tmp';w['tmux_session']='work'
        self.assertEqual(session.terminal_command(w),[
            'foot','--working-directory=/tmp','--','tmux','new-session','-A','-s','work','-c','/tmp'])
    def test_match_does_not_steal_unrelated_same_session(self):
        w=window();unrelated={**w,'address':'0x2','pid':456,'title':'other task'}
        self.assertIsNone(session.match(w,[unrelated],set(),True))
        self.assertIsNone(session.match(w,[w],{'0x1'},True))
        self.assertEqual(session.match(w,[w],set(),True),w)
    def test_snapshots_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp,patch('session.STATE',Path(tmp)):
            data=snapshot();session.commit(data,True)
            self.assertEqual(session.read(Path(tmp)/'manual.json'),data)
            self.assertEqual((Path(tmp)/'latest.json').stat().st_mode&0o777,0o600)
            new=copy.deepcopy(data);new['windows']=[];session.commit(new)
            self.assertEqual(len(list((Path(tmp)/'history').glob('*.json'))),1)

    def test_shutdown_checkpoint_preserves_nonempty_session(self):
        with tempfile.TemporaryDirectory() as tmp,patch('session.STATE',Path(tmp)),patch('session.capture') as capture:
            previous=snapshot();session.atomic(session.STATE/'latest.json',previous)
            empty=copy.deepcopy(previous);empty['windows']=[];empty['active']=None
            capture.return_value=empty
            result=session.shutdown_checkpoint()
            self.assertEqual(result,previous)
            self.assertEqual(session.read(session.STATE/'latest.json'),previous)

    def test_partial_restore_pauses_and_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp,patch('session.STATE',Path(tmp)/'state'),patch('session.RUNTIME',Path(tmp)/'run'):
            data=snapshot();data['windows'][0]['launch']={'kind':'unsupported','reason':'manual'}
            session.atomic(session.STATE/'latest.json',data)
            with patch('session.capture',return_value=snapshot()),patch('session.hypr',side_effect=lambda c:[]),patch('session.config',return_value=session.DEFAULTS):
                result=session.restore()
            self.assertEqual(result['restored'],0);self.assertTrue(result['errors']);self.assertTrue((session.RUNTIME/'pause.json').exists())
            self.assertEqual(session.read(session.STATE/'latest.json'),data)

if __name__=='__main__':unittest.main()
