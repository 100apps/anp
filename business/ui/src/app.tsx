import { useEffect, useState } from "react";
import { Activity, ArrowUpRight, Boxes, Check, GitBranch, LoaderCircle, Play, Sparkles, Terminal } from "lucide-react";
import { Button } from "./components/ui/button";
import { runDemo } from "./lib/agent-client";
import Dubbing from "./components/dubbing";

export default function App() {
  const [graph, setGraph] = useState("workflow_demo");
  const [text, setText] = useState("将存储、平台和业务分层。各组件独立部署，使用兼容版本，支持本地与外部资源切换。");
  const [events, setEvents] = useState<string[]>([]);
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("连接中");
  const [platforms, setPlatforms] = useState<{id:string;name:string;path:string;status:string}[]>([
    {id:"dbx",name:"DBX",path:"/dbx/",status:"checking"},
    {id:"langfuse",name:"Langfuse",path:"/langfuse/",status:"checking"},
    {id:"litellm",name:"LiteLLM",path:"/litellm/",status:"checking"},
    {id:"supabase",name:"Supabase",path:"/supabase/",status:"checking"}
  ]);
  useEffect(() => {fetch("/api/health").then(r => r.json()).then(r => setMode(r.model_mode)).catch(() => setMode("未连接"));}, []);
  useEffect(() => {fetch("/api/platforms").then(r=>r.json()).then(setPlatforms).catch(()=>setPlatforms(old=>old.map(p=>({...p,status:"unavailable"}))));}, []);
  async function run() {
    setBusy(true); setEvents([]); setResult("");
    try {
      const response = await runDemo(graph, text, (name, data) => {
        if (name.startsWith("updates")) setEvents(old => [...old, ...Object.keys((data || {}) as object)]);
      });
      const state = response.result as {summary?: string; messages?: {content: string}[]};
      setResult(state?.summary || state?.messages?.at(-1)?.content || JSON.stringify(state, null, 2));
    } catch (error) {setResult("运行失败：" + String(error));}
    finally {setBusy(false);}
  }
  return <div className="shell">
    <aside><a className="brand" href="/workbench/"><Boxes size={25}/><strong>ANP<span>3</span></strong></a>
      <p className="nav-label">工作空间</p><a className="nav active"><Activity size={17}/>业务工作台</a>
      <p className="nav-label">业务演示</p>
      <button className={"nav " + (graph === "video_dubbing" ? "selected" : "")} onClick={() => setGraph("video_dubbing")}><Play size={17}/>视频译配</button>
      <button className={"nav " + (graph === "workflow_demo" ? "selected" : "")} onClick={() => {setGraph("workflow_demo");setResult("");}}><GitBranch size={17}/>Workflow</button>
      <button className={"nav " + (graph === "deepagent_demo" ? "selected" : "")} onClick={() => {setGraph("deepagent_demo");setResult("");}}><Sparkles size={17}/>Deep Agent</button>
      <p className="nav-label">平台能力</p>
      {platforms.map(platform=><a key={platform.id} className="nav platform-link" href={platform.path} target="_blank" rel="noreferrer" title={platform.status==="available"?"平台可访问":platform.status==="checking"?"正在检查平台":"上游平台尚未启动或不可达"}><Boxes size={16}/><span>{platform.id==="dbx"?"DBX":platform.id==="langfuse"?"Langfuse":platform.id==="litellm"?"LiteLLM":"Supabase"}</span><small className={"platform-status "+platform.status}>{platform.status==="available"?"可访问":platform.status==="checking"?"检查中":"未就绪"}</small><ArrowUpRight size={12}/></a>)}
      <div className="aside-footer"><span className="dot"/>本地开发环境<small>存储 · 平台 · 业务</small></div>
    </aside>
    <main><header><span>工作空间 <span className="slash">/</span> 业务工作台</span><a href={import.meta.env.DEV ? "/agent/docs" : "/docs"} target="_blank" rel="noreferrer">API 文档 <ArrowUpRight size={14}/></a></header>
      {graph === "video_dubbing" ? <Dubbing/> : <section className="content"><div className="eyebrow">ANP3 WORKBENCH</div><div className="title-row"><div><h1>从一个想法，到一次运行。</h1><p className="subtitle">选择业务演示，查看执行步骤与输出结果。</p></div><span className="badge"><span className="dot"/>{mode === "demo" ? "离线演示模型" : mode === "openai" ? "真实模型" : mode}</span></div>
      <div className="cards">
        <button className={"demo-card " + (graph === "workflow_demo" ? "chosen" : "")} onClick={() => setGraph("workflow_demo")}><GitBranch className="card-icon"/><span className="card-title">Workflow<span className="card-tag">确定性流程</span></span><p>清洗文本、生成摘要，逐步查看图执行过程。</p><span className="card-link">三个节点 · 一个结果 <ArrowUpRight size={15}/></span></button>
        <button className={"demo-card " + (graph === "deepagent_demo" ? "chosen" : "")} onClick={() => setGraph("deepagent_demo")}><Sparkles className="card-icon"/><span className="card-title">Deep Agent<span className="card-tag">工具协作</span></span><p>读取架构资料，使用工具并生成一份研究报告。</p><span className="card-link">Deep Agents · 资料工具 <ArrowUpRight size={15}/></span></button>
      </div>
      <div className="workspace"><section className="panel"><div className="panel-heading"><h2>输入任务</h2><span>{graph === "workflow_demo" ? "Workflow" : "Deep Agent"}</span></div><label htmlFor="task">任务内容</label><textarea id="task" value={text} onChange={e => setText(e.target.value)} placeholder="输入一段文本或报告要求…" /><div className="run-footer"><small>{mode === "demo" ? "无需 API 密钥 · 输出仅用于演示" : "使用已配置的模型网关"}</small><Button className="run-button" onClick={run} disabled={busy || !text.trim()}>{busy ? <LoaderCircle className="spin" size={16}/> : <Play size={15}/>} {busy ? "运行中" : "运行任务"}</Button></div></section>
      <section className="panel result-panel"><div className="panel-heading"><h2>运行结果</h2><span>{busy ? "正在执行" : result ? "已完成" : "等待运行"}</span></div>{result ? <pre data-testid="result">{result}</pre> : <div className="empty"><Terminal size={27}/><p>结果将在这里呈现</p><small>点击「运行任务」开始一次执行</small></div>}</section></div>
      <section className="events"><h2>执行轨迹</h2>{events.length ? events.map((e,i) => <span key={i} className="event"><Check size={13}/>{e}</span>) : <span className="muted">尚无执行事件</span>}</section>
      <footer>由 LangGraph 与 Deep Agents 驱动 <span>ANP3 · 可插拔业务平台</span></footer></section>}
    </main>
  </div>;
}
