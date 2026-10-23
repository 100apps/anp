"""通过真实debugpy协议验证断点，并验证后端源码热重载；无需操作IDE窗口。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import queue
import socket
import threading
import time
import urllib.request
import uuid

import psutil

import check
from manage import ROOT


class Debug:
    def __init__(self,port=56796):
        self.socket=socket.create_connection(('127.0.0.1',port),timeout=10)
        self.socket.settimeout(120); self.file=self.socket.makefile('rb'); self.incoming=queue.Queue(); self.seq=0; self.pending=[]
        threading.Thread(target=self.read,daemon=True).start()

    def read(self):
        try:
            while True:
                headers={}
                while (line:=self.file.readline()) not in (b'\r\n',b''):
                    key,value=line.decode().split(':',1); headers[key.lower()]=value.strip()
                if not headers: break
                self.incoming.put(json.loads(self.file.read(int(headers['content-length']))))
        except OSError: pass

    def send(self,command,arguments):
        self.seq+=1
        data=json.dumps({'seq':self.seq,'type':'request','command':command,'arguments':arguments}).encode()
        self.socket.sendall(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data)
        return self.seq

    def wait(self,predicate):
        for index,value in enumerate(self.pending):
            if predicate(value): return self.pending.pop(index)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            value=self.incoming.get(timeout=max(0.1,deadline-time.monotonic()))
            if predicate(value): return value
            self.pending.append(value)
        raise TimeoutError('调试协议等待超时')

    def response(self,seq):
        result=self.wait(lambda value:value.get('request_seq')==seq)
        assert result.get('success'), result.get('message','调试请求失败')
        return result.get('body',{})


def health():
    with urllib.request.urlopen('http://127.0.0.1:20260/api/health',timeout=3) as response:
        return json.load(response)


def application_subprocess(event):
    if event.get('event') != 'debugpyAttach':
        return False
    try:
        arguments = psutil.Process(event['body']['subProcessId']).cmdline()
    except (KeyError, psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    # macOS/Linux 的 spawn 还会注册资源跟踪器等辅助进程；它们不执行业务图。
    # 参数只用于分类，不输出；debugpy 可能在参数中携带临时调试令牌。
    return not any(name in argument for argument in arguments
                   for name in ('multiprocessing.resource_tracker', 'multiprocessing.forkserver'))


def main():
    debug=Debug(); result={}
    debug.response(debug.send('initialize',{'adapterID':'debugpy','clientID':'anp-acceptance','pathFormat':'path',
                                            'linesStartAt1':True,'columnsStartAt1':True,'supportsRunInTerminalRequest':False}))
    debug.send('attach',{'justMyCode':True,'subProcess':True})
    debug.wait(lambda value:value.get('event')=='initialized')
    debug.response(debug.send('configurationDone',{}))
    # 热重载的应用在子进程；VSCode的subProcess=true也会自动创建此子调试会话。
    child=debug.wait(application_subprocess)['body']
    result['application_subprocess_pid']=child['subProcessId']
    parent=debug
    debug=Debug(child.get('connect',{}).get('port',56796))
    debug.response(debug.send('initialize',{'adapterID':'debugpy','clientID':'anp-child','pathFormat':'path','linesStartAt1':True,'columnsStartAt1':True}))
    debug.send('attach',{**child,'justMyCode':True})
    debug.wait(lambda value:value.get('event')=='initialized')
    source=ROOT/'business/src/anp_business/workflow_demo/nodes.py'
    breakpoint=debug.response(debug.send('setBreakpoints',{'source':{'path':str(source)},'breakpoints':[{'line':8}]}))
    assert breakpoint['breakpoints'][0]['verified']
    debug.response(debug.send('configurationDone',{}))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running=pool.submit(lambda:asyncio.run(check.demos()))
            stopped=debug.wait(lambda value:value.get('event')=='stopped')['body']
            assert stopped['reason']=='breakpoint'
            stack=debug.response(debug.send('stackTrace',{'threadId':stopped['threadId']}))
            frame=stack['stackFrames'][0]
            assert frame['line']==8 and str(frame['source']['path']).replace('\\','/').lower()==source.as_posix().lower()
            result['breakpoint']={'verified':True,'function':frame['name'],'line':frame['line']}
            debug.response(debug.send('continue',{'threadId':stopped['threadId']}))
            result['continued_demos']=running.result(timeout=60)
            debug.response(debug.send('setBreakpoints',{'source':{'path':str(source)},'breakpoints':[]}))
            print('真实子进程断点和继续执行通过',flush=True)
    finally:
        debug.response(debug.send('disconnect',{'terminateDebuggee':False})); debug.socket.close()
        parent.response(parent.send('disconnect',{'terminateDebuggee':False})); parent.socket.close()
    path=ROOT/'business/src/anp_business/server/http.py'
    original=path.read_bytes(); marker=uuid.uuid4().hex
    try:
        text=original.decode('utf-8').replace('"model_mode": model_mode()',f'"hmr_probe": "{marker}", "model_mode": model_mode()',1)
        assert marker in text
        path.write_bytes(text.encode('utf-8'))
        started=time.monotonic()
        deadline=started+90
        while time.monotonic()<deadline:
            try:
                if health().get('hmr_probe')==marker: break
            except OSError: pass
            time.sleep(1)
        else: raise AssertionError('后端未加载新的源码响应')
        result['backend_hot_reload']=True
        result['backend_reload_seconds']=round(time.monotonic()-started,2)
    finally:
        path.write_bytes(original)
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        try:
            if 'hmr_probe' not in health(): break
        except OSError: pass
        time.sleep(1)
    else: raise AssertionError('后端源码恢复后尚未重新加载')
    result['source_restored']=True
    result['scope']='验证F5所用CLI、debugpy断点、继续执行与源码热重载；未操作IDE菜单'
    (ROOT/'var/evidence').mkdir(parents=True,exist_ok=True)
    (ROOT/'var/evidence/debug-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('调试断点、继续执行、后端热重载与源码恢复通过')


if __name__=='__main__': main()
