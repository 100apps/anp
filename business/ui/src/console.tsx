import { useEffect, useState } from "react";
import { Button } from "./components/ui/button";

type Service = {id:string;group:string;port:number;state:string;pid:number|null;can_start:boolean;can_stop:boolean;note:string};
type Policy = {id:string;name:string;mode:string;note:string};
type State = {unified:boolean;ingress:{host:string;port:number};managed:boolean;services:Service[];platforms:Record<string,string>;mapping_revision:string;revision:string;configuration:unknown;authentication:Policy[];note:string};
const serviceNames:Record<string,string> = {postgres:"PostgreSQL",redis:"Redis",clickhouse:"ClickHouse",rustfs:"RustFS",auth:"Supabase Auth",postgrest:"Supabase Data API","storage-api":"Supabase Storage",meta:"Postgres Meta",studio:"Supabase Studio","langfuse-web":"Langfuse Web","langfuse-worker":"Langfuse Worker",litellm:"LiteLLM",dbx:"DBX",business:"Agent Server（统一入口）",loki:"Loki",tempo:"Tempo",prometheus:"Prometheus",otelcol:"OTel Collector",grafana:"Grafana"};
const labels:Record<string,string> = {ready:"运行正常",unhealthy:"未就绪",stopped:"已停止",external:"外部存储",conflict:"端口被占用","not-installed":"未安装"};
async function api(path:string, method="GET", body?:unknown) {
  const response = await fetch("/api/console/"+path, {method, headers:body ? {"Content-Type":"application/json"}:undefined, body:body ? JSON.stringify(body):undefined, cache:"no-store"});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "请求失败");
  return value;
}
export function Console() {
  const [state,setState] = useState<State|null>(null);
  const [tab,setTab] = useState("services");
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [busy,setBusy] = useState(false);
  const [oauth,setOAuth] = useState("");
  const [oauthRevision,setOAuthRevision] = useState("");
  const [config,setConfig] = useState("");
  const [host,setHost] = useState("127.0.0.1");
  const [mapping,setMapping] = useState<Record<string,string>>({});
  const [credentials,setCredentials] = useState<Record<string,{username:string;password:string;note:string}>>({});
  async function refresh(reset=false) {
    try {const value:State = await api("state");setState(value);if(reset){setHost(value.ingress.host);setMapping(value.platforms);setConfig(JSON.stringify(value.configuration,null,2));}}
    catch(e){setError(String(e));}
  }
  useEffect(()=>{void api("oauth").then(value=>{setOAuth(JSON.stringify(value.configuration,null,2));setOAuthRevision(value.revision);}).catch(e=>setError(String(e)));void refresh(true);const timer=setInterval(()=>void refresh(),10000);return()=>clearInterval(timer);},[]);
  useEffect(()=>{const clear=()=>setCredentials({});document.addEventListener("visibilitychange",clear);const timer=setInterval(clear,60000);return()=>{clearInterval(timer);document.removeEventListener("visibilitychange",clear);};},[]);
  async function operation(service:Service,action:string) {
    setBusy(true);setError("");setNotice(`${service.id} 正在${action==="start"?"启动":"停止"}…`);
    try {
      const job=await api(`services/${service.id}/${action}`,"POST");
      for(let count=0;count<900;count++){
        await new Promise(resolve=>setTimeout(resolve,2000));
        const result=await api(`jobs/${job.id}`);
        if(result.state!=="running") {if(result.state!=="succeeded")throw new Error(result.detail);setNotice(`${service.id} 操作完成`);await refresh();return;}
      }
      throw new Error("等待超时，请刷新状态确认");
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function save(kind:string) {
    setBusy(true);setError("");setNotice("");
    try {const value = await api(kind,"PUT",kind==="mappings"?{platforms:mapping,host,revision:state?.mapping_revision}:{configuration:JSON.parse(config),revision:state?.revision});setNotice(value.detail);await refresh(true);}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  if(!state)return <section className="content" role="status">{error || "正在读取本机服务状态…"}</section>;
  return <section className="content console-content"><div className="eyebrow">ANP CONSOLE</div><div className="title-row"><div><h1>平台与存储控制台</h1><p className="subtitle">{state.note}</p></div><Button variant="outline" onClick={()=>void refresh(true)} disabled={busy}>刷新</Button></div>
    <div className="console-tabs" role="tablist">{[["services","服务状态"],["mappings","端口映射"],["configuration","运行配置"],["access","登录与凭据"],["oauth","OAuth 登录"]].map(([id,label])=><button key={id} role="tab" aria-selected={tab===id} onClick={()=>{setTab(id);setCredentials({});}}>{label}</button>)}</div>
    {error&&<p className="login-message" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {tab==="services"&&<>{!state.managed&&<p>Business 独立运行：可以配置平台地址，服务启停请在平台主机上操作。</p>}{["storage","platform","observability"].map(group=><section className="console-group" key={group}><h2>{group==="storage"?"存储能力":group==="platform"?"平台能力":"可观测性"}</h2><div className="console-services">{state.services.filter(s=>s.group===group).map(s=><article key={s.id} className="console-service"><div className="console-service-title"><strong>{serviceNames[s.id]||s.id}</strong><span className={"service-state "+s.state}>{labels[s.state]||s.state}</span></div><p>127.0.0.1:{s.port}{s.pid?` · PID ${s.pid}`:""}</p><p className="console-note">{s.note||"仅管理本解压目录持有的进程"}</p><div className="console-actions">{s.id==="rustfs"&&<a className="console-link" href="/rustfs/console/" target="_blank" rel="noreferrer">打开后台 ↗</a>}<Button variant="outline" disabled={busy||!s.can_start} onClick={()=>void operation(s,"start")}>启动</Button><Button variant="outline" disabled={busy||!s.can_stop} onClick={()=>void operation(s,"stop")}>停止</Button></div></article>)}</div></section>)}</>}
    {tab==="mappings"&&<section className="panel"><h2>HTTP / WebSocket 代理</h2><p>入口路径固定，修改上游后即时生效。管理页面代理验证管理员身份；SDK API 保留自身凭据校验。{state.unified?"当前使用 Agent Server 单入口：Studio 同源打开，LiteLLM 使用 /litellm/。":"独立 Business 未启用统一入口：Studio 与 LiteLLM 管理入口跳转到配置地址。"}</p>{Object.entries(mapping).map(([id,value])=><label className="console-field" key={id}><span>{id==="rustfs_api"?"RustFS S3 管理 API（保留签名路径）":`/${id}/ → 上游地址`}</span><input value={value} onChange={e=>setMapping({...mapping,[id]:e.target.value})}/></label>)}<label className="console-field"><span>Agent Server 入口监听（端口 {state.ingress.port}，重启后生效）</span><select value={host} onChange={e=>setHost(e.target.value)}><option value="127.0.0.1">仅本机 127.0.0.1</option><option value="0.0.0.0">局域网 0.0.0.0（仅开放已鉴权的 Agent Server）</option></select></label><Button disabled={busy} onClick={()=>void save("mappings")}>保存映射</Button></section>}
    {tab==="configuration"&&<section className="panel"><h2>原生运行配置</h2><p>仅展示可编辑的监听端口和观测配置。密码、JWT、外部资源连接串不在此返回。运行中服务的端口不能直接修改，控制台依赖服务需在终端维护。</p><textarea className="console-json" aria-label="运行配置 JSON" value={config} onChange={e=>setConfig(e.target.value)} disabled={!state.managed}/><Button disabled={busy||!state.managed} onClick={()=>void save("configuration")}>保存运行配置</Button></section>}
    {tab==="oauth"&&<section className="panel"><h2>OAuth / OpenID Connect</h2><p>默认全部留空、不启用。可配置 Google 或标准 OIDC 提供方。issuer 留空关闭；redirect_uri 必须为完整 /api/auth/oauth/callback 地址。client_secret_file / service_key_file 只填写服务端私有文件路径，不填写密钥。原生包自动使用本包 Supabase 服务密钥；独立 Business 需配置 service_key_file。</p><p>admin_subjects 为有管理员权限的 OIDC sub 清单，默认空；其他 OAuth 用户只拥有普通业务权限。不会根据相同邮箱接管现有管理员。</p><textarea className="console-json" aria-label="OAuth 配置 JSON" value={oauth} onChange={e=>setOAuth(e.target.value)}/><Button disabled={busy} onClick={async()=>{setBusy(true);setError("");try{const result=await api("oauth","PUT",{configuration:JSON.parse(oauth),revision:oauthRevision});setNotice(result.detail);const value=await api("oauth");setOAuthRevision(value.revision);}catch(e){setError(String(e));}finally{setBusy(false);}}}>保存 OAuth 配置</Button></section>}
    {tab==="access"&&<div className="console-services">{state.authentication.map(p=><article key={p.id} className="console-service"><div className="console-service-title"><strong>{p.name}</strong><span>{p.mode}</span></div><p className="console-note">{p.note}</p><div className="console-actions"><a className="console-link" href={`/${p.id}/`} target="_blank" rel="noreferrer">打开平台 ↗</a>{p.mode==="手动登录"&&<Button variant="outline" onClick={async()=>{try{if(credentials[p.id]){setCredentials(old=>{const next={...old};delete next[p.id];return next;});}else{const value=await api(`credentials/${p.id}`,"POST");setCredentials(old=>({...old,[p.id]:value}));}}catch(e){setError(String(e));}}}>{credentials[p.id]?"隐藏凭据":"查看登录凭据"}</Button>}</div>{credentials[p.id]&&<div className="console-credentials"><label>用户名<input readOnly value={credentials[p.id].username}/></label><label>密码<input readOnly value={credentials[p.id].password}/></label><small>{credentials[p.id].note} 页面隐藏或 60 秒后自动收起。</small></div>}</article>)}</div>}
  </section>;
}
