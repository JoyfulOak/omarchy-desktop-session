#!/usr/bin/python
"""User-owned Omarchy session checkpoint/restore, for Hyprland's Lua API."""
import argparse, contextlib, fcntl, hashlib, json, os, re, shutil, signal
import subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'omarchy/session-restore'
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'omarchy/session-restore.json'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))/'omarchy-session-restore'
DEFAULTS = {'auto_save':True, 'auto_restore':True, 'exact_geometry':False,
            'excluded_classes':['org.omarchy.SessionRestore','org.omarchy.screensaver']}
TERMINALS = {'foot':'foot','Alacritty':'alacritty','kitty':'kitty','com.mitchellh.ghostty':'ghostty','ghostty':'ghostty'}


def atomic(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(data,out,indent=2,ensure_ascii=False); out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def read(path, default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default


def config():return {**DEFAULTS,**(read(CONFIG,{}) or {})}


@contextlib.contextmanager
def lock(block=True):
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (RUNTIME/'operation.lock').open('a') as file:
        fcntl.flock(file,fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        yield


def hypr(command):
    p=subprocess.run(['hyprctl','-j',command],capture_output=True,text=True,timeout=5)
    if p.returncode:raise RuntimeError(p.stderr or p.stdout)
    return json.loads(p.stdout)


def lua(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,dict):return '{'+','.join(k+'='+lua(v) for k,v in value.items())+'}'
    # Lua has different unicode escape syntax; encode as literal UTF-8.
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('\n','\\n').replace('\r','\\r').replace('\0','\\000')+'"'


def dispatch(method, **kwargs):
    if not re.fullmatch(r'(window\.(move|float|resize|fullscreen_state|pin)|workspace\.move|focus)',method):raise ValueError('Invalid dispatcher')
    expression=f'hl.dsp.{method}({lua(kwargs)})'
    p=subprocess.run(['hyprctl','dispatch',expression],capture_output=True,text=True,timeout=5)
    if p.returncode or p.stdout.strip()!='ok':raise RuntimeError(expression+': '+p.stdout+p.stderr)


def desktop_apps():
    import gi
    from gi.repository import Gio
    apps=[a for a in Gio.AppInfo.get_all() if isinstance(a,Gio.DesktopAppInfo)]
    # Gio.AppInfo.get_all() intentionally omits Hidden desktop entries. Some
    # applications keep a real executable launcher hidden alongside a visible
    # wrapper (for example, a mise task launcher). Include those entries so
    # window identity can select the executable that actually owns the window.
    known={a.get_id() for a in apps}
    data_home=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    data_dirs=[data_home,*[Path(p) for p in os.environ.get('XDG_DATA_DIRS','/usr/local/share:/usr/share').split(':') if p]]
    for directory in data_dirs:
        applications=directory/'applications'
        if not applications.is_dir():continue
        for filename in applications.rglob('*.desktop'):
            try:contents=filename.read_text(errors='replace')
            except OSError:continue
            if not re.search(r'^Hidden\s*=\s*true\s*$',contents,re.I|re.M):continue
            try:app=Gio.DesktopAppInfo.new_from_filename(str(filename))
            except (TypeError,ValueError):continue
            if app and app.get_id() not in known:
                apps.append(app);known.add(app.get_id())
    return apps


def identity(client,apps):
    cls=client.get('initialClass') or client.get('class','')
    if cls in TERMINALS:return {'kind':'terminal','program':TERMINALS[cls]}
    # Prefer the desktop entry whose executable matches this window's actual
    # process. A wrapper may advertise the same StartupWMClass as its hidden
    # executable launcher, but fail when invoked outside its project directory.
    exe=process_executable(client)
    executable_matches=[a for a in apps if exe and Path(a.get_executable() or '').name==exe]
    if len(executable_matches)==1:return {'kind':'desktop','id':executable_matches[0].get_id()}
    candidates=[]
    for app in apps:
        keys=[app.get_startup_wm_class(),app.get_id().removesuffix('.desktop')]
        if cls.casefold() in [s.casefold() for s in keys if s]:candidates.append(app)
    if len(candidates)==1:return {'kind':'desktop','id':candidates[0].get_id()}
    # Only an unambiguous desktop executable match; never replay /proc cmdlines.
    return {'kind':'unsupported','reason':'No unambiguous desktop launcher; reopen manually.'}


def process_executable(client):
    try:return Path(f"/proc/{client['pid']}/exe").resolve().name
    except (OSError,KeyError):return ''


def terminal_cwd(pid):
    # Shell cwd is useful; shell commands are deliberately not replayed.
    try:
        children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
        for child in children:
            if Path(f'/proc/{child}/comm').read_text().strip() in {'bash','zsh','fish','sh','nu'}:
                return str(Path(f'/proc/{child}/cwd').resolve(strict=True))
        return str(Path(f'/proc/{pid}/cwd').resolve(strict=True))
    except OSError:return str(Path.home())


def terminal_tmux_session(pid):
    """Return the tmux target for a terminal shell, when it has one."""
    try:
        children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
        candidates=[str(pid),*children]
        for child in children:
            candidates.extend(Path(f'/proc/{child}/task/{child}/children').read_text().split())
        for candidate in candidates:
            environ=Path(f'/proc/{candidate}/environ').read_bytes().split(b'\0')
            values={item.partition(b'=')[0]:item.partition(b'=')[2] for item in environ if b'=' in item}
            if b'TMUX' not in values or b'TMUX_PANE' not in values:continue
            pane=values[b'TMUX_PANE'].decode(errors='replace')
            result=subprocess.run(['tmux','display-message','-p','-t',pane,'#{session_name}'],
                                  capture_output=True,text=True,timeout=2)
            target=result.stdout.strip()
            if result.returncode==0 and target:return target
    except (OSError,subprocess.SubprocessError):pass
    return None


def capture(apps=None):
    cfg=config(); clients=hypr('clients'); monitors=hypr('monitors'); workspaces=hypr('workspaces'); active=hypr('activewindow')
    by_id={m['id']:m for m in monitors}; apps=desktop_apps() if apps is None else apps
    windows=[]
    for c in clients:
        cls=c.get('initialClass') or c.get('class','')
        if not c.get('mapped') or not cls or cls in cfg['excluded_classes']:continue
        w={k:c.get(k) for k in ('address','pid','class','initialClass','title','workspace','floating','at','size','fullscreen','fullscreenClient','pinned','grouped')}
        w['monitor']=by_id.get(c['monitor'],{}).get('name','')
        w['launch']=identity(c,apps)
        if w['launch']['kind']=='terminal':
            w['cwd']=terminal_cwd(c['pid'])
            tmux_session=terminal_tmux_session(c['pid'])
            if tmux_session:w['tmux_session']=tmux_session
        windows.append(w)
    windows.sort(key=lambda w:(w['workspace']['name'],w['at'][0],w['at'][1],w['address']))
    return {'format':1,'saved_at':datetime.now(timezone.utc).isoformat(),'session':os.environ.get('HYPRLAND_INSTANCE_SIGNATURE',''),
            'windows':windows,'monitors':monitors,'workspaces':workspaces,'active':active.get('address')}


def fingerprint(data):
    return json.dumps({'windows':[{k:v for k,v in w.items() if k not in ('title','grouped')} for w in data['windows']],
        'monitors':[{k:m.get(k) for k in ('name','width','height','scale','x','y','activeWorkspace','focused')} for m in data['monitors']],
        'active':data['active']},sort_keys=True)


def commit(data,manual=False):
    old=read(STATE/'latest.json')
    if manual or not old or fingerprint(data)!=fingerprint(old):
        if old:
            history=STATE/'history'; history.mkdir(parents=True,exist_ok=True,mode=0o700)
            atomic(history/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json'),old)
            for path in sorted(history.glob('*.json'))[:-20]:path.unlink()
        atomic(STATE/'latest.json',data)
    if manual:atomic(STATE/'manual.json',data)
    return data


def save():
    with lock():return commit(capture(),True)


def checkpoint():
    """Save the current desktop without creating a manual snapshot."""
    with lock():return commit(capture())


def shutdown_checkpoint():
    """Save shutdown state while retaining windows already lost to teardown."""
    with lock():
        data=capture(); previous=read(STATE/'latest.json')
        if previous and previous.get('windows'):
            current={w['address']:w for w in data.get('windows',[])}
            old={w['address']:w for w in previous['windows']}
            missing=old.keys()-current.keys()
            if missing:
                data['windows']=list(current.values())+[old[address] for address in sorted(missing)]
                data['windows'].sort(key=lambda w:(w['workspace']['name'],w['at'][0],w['at'][1],w['address']))
                if not data.get('active') and previous.get('active') in old:
                    data['active']=previous['active']
                print(f'Preserved {len(missing)} window(s) already closed during shutdown',flush=True)
        return commit(data)


def workspace_selector(ws):
    name=ws['name']
    if name.startswith('special:') or name=='special':return name
    if name==str(ws['id']) and ws['id']>0:return name
    return 'name:'+name


def geometry(w,old_monitors,new_monitors):
    target=next((m for m in new_monitors if m['name']==w['monitor']),None) or next((m for m in new_monitors if m.get('focused')),new_monitors[0])
    old=next((m for m in old_monitors if m['name']==w['monitor']),target)
    def logical(m):
        width,height=m['width'],m['height']
        if m.get('transform',0)%2:width,height=height,width
        return width/m['scale'],height/m['scale']
    ow,oh=logical(old); nw,nh=logical(target)
    width=max(100,min(round(w['size'][0]*nw/ow),round(nw)))
    height=max(80,min(round(w['size'][1]*nh/oh),round(nh)))
    x=target['x']+round((w['at'][0]-old['x'])*nw/ow)
    y=target['y']+round((w['at'][1]-old['y'])*nh/oh)
    return target,max(target['x'],min(x,target['x']+round(nw)-width)),max(target['y'],min(y,target['y']+round(nh)-height)),width,height


def place(w,live,snapshot,cfg):
    selector='address:'+live['address']; monitors=hypr('monitors')
    target,x,y,width,height=geometry(w,snapshot['monitors'],monitors)
    ws=workspace_selector(w['workspace'])
    dispatch('window.fullscreen_state',window=selector,internal=0,client=0,action='set')
    dispatch('window.move',window=selector,workspace=ws,follow=False)
    if not ws.startswith('special'):dispatch('workspace.move',workspace=ws,monitor=target['name'])
    floating=w['floating'] or cfg['exact_geometry']
    current=next(c for c in hypr('clients') if c['address']==live['address'])
    if current['floating'] != floating:
        dispatch('window.float',window=selector,action='set' if floating else 'unset')
    if floating:
        dispatch('window.resize',window=selector,x=width,y=height,relative=False)
        dispatch('window.move',window=selector,x=x,y=y,relative=False)
        current=next(c for c in hypr('clients') if c['address']==live['address'])
        if bool(current.get('pinned')) != bool(w.get('pinned')):
            dispatch('window.pin',window=selector)
    if w.get('fullscreen') or w.get('fullscreenClient'):
        dispatch('window.fullscreen_state',window=selector,internal=w.get('fullscreen',0),client=w.get('fullscreenClient',0),action='set')


def terminal_command(w):
    program=w['launch']['program']; cwd=w.get('cwd',str(Path.home()))
    if program not in TERMINALS.values():raise ValueError('Unknown terminal')
    if not Path(cwd).is_dir():cwd=str(Path.home())
    if w.get('tmux_session'):
        target=w['tmux_session']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',target):raise ValueError('Invalid tmux session target')
        command=['tmux','new-session','-A','-s',target,'-c',cwd]
        return ([program,'--working-directory='+cwd,'--',*command] if program=='foot'
                else [program,'--working-directory',cwd,'--',*command])
    if program=='foot':return ['foot','--working-directory='+cwd]
    if program in ('alacritty','kitty'):return [program,'--working-directory',cwd]
    return ['ghostty','--working-directory='+cwd]


def launch(w):
    descriptor=w['launch']
    if descriptor['kind']=='terminal':command=terminal_command(w)
    elif descriptor['kind']=='desktop':
        from gi.repository import Gio
        app=next((a for a in desktop_apps() if a.get_id()==descriptor['id']),None)
        if app is None:app=Gio.DesktopAppInfo.new(descriptor['id'])
        if not app:raise RuntimeError('Desktop launcher no longer installed: '+descriptor['id'])
        command=['/usr/bin/gio','launch',app.get_filename()]
    else:raise RuntimeError(descriptor.get('reason','Unsupported application'))
    # Keep launched apps independent of this daemon, including forked GUI children.
    subprocess.run(['systemd-run','--user','--collect','--quiet',
        '--property=PartOf=graphical-session.target','--property=ExitType=cgroup','--',*command],
        capture_output=True,text=True,check=True,timeout=10)


def match(w,clients,used,same_session=False):
    cls=w.get('initialClass') or w['class']
    choices=[c for c in clients if c['address'] not in used and (c.get('initialClass') or c['class'])==cls]
    if same_session:
        exact=next((c for c in choices if c['address']==w['address'] and c['pid']==w['pid']),None)
        if exact:return exact
    exact=next((c for c in choices if c['title']==w['title']),None)
    if same_session:return None
    return exact or (choices[0] if choices else None)


def restore(path=None):
    with lock():
        snapshot=read(path or STATE/'latest.json')
        if not snapshot or snapshot.get('format')!=1:raise RuntimeError('No compatible saved session yet.')
        # A previous restore may have paused autosaving because an unsupported
        # window could not be relaunched.  Re-evaluate the current snapshot on
        # every login instead of carrying that stale pause into a new session.
        (RUNTIME/'pause.json').unlink(missing_ok=True)
        atomic(STATE/'before-restore.json',capture())
        cfg=config(); errors=[]; used=set(); restored={}; count=0; pending=[]
        restore_key=hashlib.sha256((os.environ.get('HYPRLAND_INSTANCE_SIGNATURE','')+json.dumps(snapshot,sort_keys=True)).encode()).hexdigest()
        mapping_path=RUNTIME/('restored-'+restore_key+'.json'); mapping=read(mapping_path,{})
        same_session=snapshot['session']==os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
        clients=hypr('clients')
        for w in snapshot['windows']:
            if (w.get('initialClass') or w['class']) in cfg['excluded_classes']:continue
            try:
                previous=mapping.get(w['address'])
                live=next((c for c in clients if previous and c['address']==previous['address'] and c['pid']==previous['pid'] and c['address'] not in used),None)
                if not live:live=match(w,clients,used,same_session)
                if not live:
                    launch(w)
                    pending.append((w,{c['address'] for c in clients},time.monotonic()+18))
                    continue
                used.add(live['address']); restored[w['address']]=live['address']
                mapping[w['address']]={'address':live['address'],'pid':live['pid']}
                atomic(mapping_path,mapping)
                place(w,live,snapshot,cfg); count+=1
            except Exception as error:errors.append((w.get('class') or 'window')+': '+str(error))
        # Start all missing applications before waiting for any one of them.
        # This prevents a slow first app from delaying every later workspace.
        while pending:
            clients=hypr('clients'); remaining=[]
            for w,before,deadline in pending:
                try:
                    live=match(w,clients,used|before)
                    if live:
                        used.add(live['address']); restored[w['address']]=live['address']
                        mapping[w['address']]={'address':live['address'],'pid':live['pid']}
                        atomic(mapping_path,mapping)
                        place(w,live,snapshot,cfg); count+=1
                    elif time.monotonic()>=deadline:
                        errors.append((w.get('class') or 'window')+': No new window appeared (the app may be single-instance or use a different window class).')
                    else:remaining.append((w,before,deadline))
                except Exception as error:errors.append((w.get('class') or 'window')+': '+str(error))
            pending=remaining
            if pending:time.sleep(.2)
        # Restore each monitor's visible workspace, then the saved focused window.
        connected={m['name'] for m in hypr('monitors')}
        for m in snapshot['monitors']:
            if m['name'] in connected:
                try:dispatch('focus',workspace=workspace_selector(m['activeWorkspace']))
                except Exception as error:errors.append(str(error))
        if snapshot['active'] in restored:
            try:dispatch('focus',window='address:'+restored[snapshot['active']])
            except Exception as error:errors.append(str(error))
        result={'at':datetime.now(timezone.utc).isoformat(),'restored':count,'total':len(snapshot['windows']),'errors':errors}
        atomic(STATE/'restore-result.json',result)
        # Unsupported windows are expected for custom launchers and should not
        # disable autosaving.  Pause only for failures that indicate the
        # restore machinery itself needs attention.
        actionable=[error for error in errors if 'No unambiguous desktop launcher' not in error]
        if actionable:
            atomic(RUNTIME/'pause.json',{'reason':'Restore incomplete. Save manually or resume autosave after reviewing the results.'})
        return result


class Stabilizer:
    """Debounce normal edits and hold window removals across shutdown teardown."""
    def __init__(self,saved=None):self.saved=saved; self.pending=None; self.since=0
    def observe(self,data,now):
        key=fingerprint(data)
        if self.saved and fingerprint(self.saved)==key:self.pending=None;return None
        if self.pending is None or fingerprint(self.pending)!=key:self.pending=data;self.since=now
        old={w['address'] for w in (self.saved or {}).get('windows',[])}
        new={w['address'] for w in data['windows']}
        # Give window removals time to settle because logind/Hyprland can
        # briefly report the desktop as empty during shutdown. Normal layout
        # additions and edits settle quickly.
        removed = bool(old - new)
        delay=20 if removed else 3
        if now-self.since>=delay:
            self.saved=data;self.pending=None;return data
        return None


def daemon():
    from gi.repository import Gio, GLib
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    daemon_lock=(RUNTIME/'daemon.lock').open('a')
    try:fcntl.flock(daemon_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    stopping=False; shutdown=False; final_saved=False
    def checkpoint_before_exit():
        nonlocal final_saved
        if final_saved or not config()['auto_save'] or (RUNTIME/'pause.json').exists():return
        try:
            shutdown_checkpoint();final_saved=True
            print('Saved final shutdown checkpoint',flush=True)
        except Exception as error:print('Shutdown checkpoint:',error,flush=True)
    def stop(*_):
        nonlocal stopping;stopping=True
        checkpoint_before_exit()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def preparing(_bus,_sender,_path,_interface,_signal,parameters):
        nonlocal shutdown;shutdown=parameters.unpack()[0]
        if shutdown:checkpoint_before_exit()
    bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
    bus.signal_subscribe('org.freedesktop.login1','org.freedesktop.login1.Manager','PrepareForShutdown','/org/freedesktop/login1',None,Gio.DBusSignalFlags.NONE,preparing)
    signature=os.environ.get('HYPRLAND_INSTANCE_SIGNATURE','')
    marker=RUNTIME/('started-'+hashlib.sha256(signature.encode()).hexdigest()[:16])
    if not marker.exists():
        atomic(marker,{'started':time.time()})
        if config()['auto_restore'] and (STATE/'latest.json').exists():
            result=restore()
            subprocess.run(['notify-send','Desktop Session',f"Restored {result['restored']} of {result['total']} windows."+(' Open Desktop Session to review issues.' if result['errors'] else '')],check=False)
    apps=desktop_apps(); guard=Stabilizer(read(STATE/'latest.json')); refreshed=time.monotonic()
    while not stopping:
        while GLib.MainContext.default().iteration(False):pass
        try:
            if not shutdown and config()['auto_save'] and not (RUNTIME/'pause.json').exists():
                with lock(False):
                    # A manual save must also reset the daemon's debounce baseline.
                    current=read(STATE/'latest.json')
                    if current and (not guard.saved or current['saved_at']!=guard.saved['saved_at']):guard=Stabilizer(current)
                    if time.monotonic()-refreshed>60:apps=desktop_apps();refreshed=time.monotonic()
                    value=guard.observe(capture(apps),time.monotonic())
                    if value:commit(value)
        except BlockingIOError:guard=Stabilizer(read(STATE/'latest.json'))
        except Exception as error:print('Checkpoint:',error,flush=True)
        for _ in range(10):
            if stopping:break
            while GLib.MainContext.default().iteration(False):pass
            time.sleep(.2)


def status():
    data=read(STATE/'latest.json',{})
    return {'saved_at':data.get('saved_at'),'windows':len(data.get('windows',[])),
            'unsupported':[w['class'] for w in data.get('windows',[]) if w['launch']['kind']=='unsupported'],
            'paused':read(RUNTIME/'pause.json'), 'settings':config(),'last_restore':read(STATE/'restore-result.json'), 'path':str(STATE/'latest.json')}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['save','restore','daemon','status','resume','toggle']);parser.add_argument('--file');args=parser.parse_args()
    if args.action=='save':
        data=save();(RUNTIME/'pause.json').unlink(missing_ok=True);print(f"Saved {len(data['windows'])} windows")
    elif args.action=='restore':print(json.dumps(restore(args.file),indent=2))
    elif args.action=='daemon':daemon()
    elif args.action=='resume':(RUNTIME/'pause.json').unlink(missing_ok=True)
    elif args.action=='toggle':
        cfg=config();enabled=not (cfg['auto_save'] and cfg['auto_restore'])
        cfg['auto_save']=enabled;cfg['auto_restore']=enabled
        atomic(CONFIG,cfg)
        if enabled:(RUNTIME/'pause.json').unlink(missing_ok=True)
        print('enabled' if enabled else 'disabled')
    else:print(json.dumps(status(),indent=2))

if __name__=='__main__':main()
