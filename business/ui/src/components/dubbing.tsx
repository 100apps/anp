import { useEffect, useState } from "react";
import { Film, LoaderCircle, Play, RotateCcw, Square } from "lucide-react";
import { Button } from "./ui/button";

type Run = {id: string; project_id: string; status: string; current_stage?: string; error?: string; created_at: string};
type Segment = {id: string; ordinal: number; text: string; start_seconds: number; end_seconds: number};
type Translation = {id: string; segment_id: string; text: string; version: number};
type Detail = {run: Run; project: {title: string}; segments: Segment[]; translations: Translation[];
  attempts: {id: string; stage: string; status: string; attempt: number; error?: string}[];
  reviews: {id: string; kind: string; verdict: string; evidence: {summary?: string}}[];
  assets: {id: string; kind: string}[]; syntheses: unknown[]; voices: unknown[];
  deliverables: {id: string; asset_id: string; manifest: {not_measured: string[]}}[]};
const statuses: Record<string, string> = {queued:"待启动", running:"执行中", failed:"失败，可重试", cancelled:"已取消",
  ready_for_review:"成片待视听复核", approved:"已审核", succeeded:"成功", passed:"通过", not_measured:"未测"};
const stages: Record<string, string> = {acquire:"获取原片",source_text:"源稿识别",background:"背景分离",translate:"中文翻译",
  review_translation:"独立复核与返修",voices:"固定音色",synthesize:"分段配音",audio_review:"回转录核对",mix:"混音封装",finish:"解码与响度检查"};
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch("/api/dubbing" + path, {headers:{"Content-Type":"application/json"}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  return body;
}

export default function Dubbing() {
  const [source, setSource] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Detail>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      const list = await api<Run[]>("/runs"); setRuns(list);
      if (selected) setDetail(await api<Detail>("/runs/" + selected));
      setError(previous => previous.startsWith("读取运行失败：") ? "" : previous);
    } catch (e) {setError("读取运行失败：" + String(e));}
  }
  useEffect(() => {void refresh(); const timer = setInterval(() => void refresh(), 2500); return () => clearInterval(timer);}, [selected]);
  async function create() {
    setBusy(true); setError("");
    try {
      const run = await api<Run>("/runs", {method:"POST",body:JSON.stringify({input:source,title:"视频译配",subtitle:subtitle || null,request_key:crypto.randomUUID()})});
      setSelected(run.id);
      await api("/runs/" + run.id + "/start", {method:"POST"});
      await refresh();
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }
  async function action(name: string) {
    setError("");
    try {await api("/runs/" + selected + "/" + name,{method:"POST"}); await refresh();} catch(e) {setError(String(e));}
  }
  const output = detail?.deliverables.at(-1);
  return <section className="content dubbing"><div className="eyebrow">VIDEO DUBBING</div>
    <div className="title-row"><div><h1>视频译配工作流</h1><p className="subtitle">从 URL 或本地原片，到中文配音、双语字幕与成片。</p></div><span className="badge">Supabase · LangGraph · Deep Agents</span></div>
    <section className="panel dub-input"><div className="panel-heading"><h2>创建译配项目</h2><Film size={18}/></div>
      <label htmlFor="video-source">视频 URL 或允许目录内的本地文件路径</label><input id="video-source" value={source} onChange={e=>setSource(e.target.value)} placeholder="https://www.youtube.com/watch?v=… 或本地文件路径"/>
      <label htmlFor="source-subtitle">可选：英文 SRT 路径；留空则实际调用 ASR</label><input id="source-subtitle" value={subtitle} onChange={e=>setSubtitle(e.target.value)} placeholder="字幕仍保留来源与待审核状态"/>
      <div className="run-footer"><small>真实模型调用 · 私有对象存储 · 本机开发身份</small><Button onClick={create} disabled={busy || !source.trim()}>{busy?<LoaderCircle className="spin" size={15}/>:<Play size={15}/>}创建并运行</Button></div>
    </section>
    {error && <p className="dub-error" role="alert">{error}</p>}
    <div className="dub-layout"><section className="panel dub-history"><h2>运行记录</h2>{runs.length ? runs.map(run=><button key={run.id} className={"dub-run "+(selected===run.id?"selected":"")} onClick={()=>{setSelected(run.id);setDetail(undefined);setError("");}}><strong>{statuses[run.status] || run.status}</strong><small>{new Date(run.created_at).toLocaleString("zh-CN")}</small><code>{run.id.slice(0,8)}</code></button>):<p className="muted">暂无运行。首次使用请先执行 make dub-migrate。</p>}</section>
    <section className="panel dub-detail">{detail ? <><div className="panel-heading"><h2>{detail.project.title}</h2><span>{statuses[detail.run.status]}</span></div>
      <div className="dub-objects"><span>原文段 {detail.segments.length}</span><span>译稿版本 {detail.translations.length}</span><span>固定音色 {detail.voices.length}</span><span>配音候选 {detail.syntheses.length}</span><span>私有产物 {detail.assets.length}</span></div>
      <p className="muted">项目 → 来源 → 原文段 → 译稿版本 → 配音候选 → 成片；音色、审核与阶段回执关联到具体对象。</p>
      <div className="dub-actions">{["failed","queued","running"].includes(detail.run.status)&&<Button onClick={()=>action("start")} disabled={detail.run.status==="running"}><RotateCcw size={14}/>{detail.run.status==="failed"?"从失败阶段重试":"启动"}</Button>}{["running","queued","failed"].includes(detail.run.status)&&<Button variant="outline" onClick={()=>action("cancel")}><Square size={14}/>取消后续执行</Button>}</div>
      {detail.run.error && <p className="dub-error">{detail.run.error}</p>}
      <div className="dub-stages">{detail.attempts.map(a=><div key={a.id}><strong>{stages[a.stage] || a.stage}</strong><span>{statuses[a.status] || a.status} · 第 {a.attempt} 次</span></div>)}</div>
      {output && <div className="dub-output"><h2>技术成片 · 待视听复核</h2><video controls preload="metadata" src={"/api/dubbing/assets/"+output.asset_id+"/content"}/><a href={"/api/dubbing/assets/"+output.asset_id+"/content"} download>下载中文配音成片</a><p className="muted">尚未测量：{output.manifest.not_measured.join("、")}。成功封装不代表整片质量审核通过。</p></div>}
      <h2>原文与译稿</h2><div className="dub-segments">{[...detail.segments].sort((a,b)=>a.ordinal-b.ordinal).map(segment=>{const versions=detail.translations.filter(t=>t.segment_id===segment.id).sort((a,b)=>a.version-b.version);return <article key={segment.id}><small>{segment.start_seconds.toFixed(2)}–{segment.end_seconds.toFixed(2)} 秒</small><p>{segment.text}</p><p>{versions.at(-1)?.text || "等待翻译"}</p><small>{versions.length?`${versions.length} 个不可变译稿版本`:""}</small></article>;})}</div>
      <h2>审核记录</h2>{detail.reviews.map(r=><p className="dub-review" key={r.id}><code>{r.kind}</code> · {statuses[r.verdict] || r.verdict}{r.evidence.summary && " · "+r.evidence.summary}</p>)}
    </>:<div className="empty"><Film/><p>选择一次运行查看对象与进度</p></div>}</section></div>
  </section>;
}
