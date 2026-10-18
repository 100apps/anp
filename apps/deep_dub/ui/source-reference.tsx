import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Input } from '@console/components/ui/input'
import { Badge } from '@console/components/ui/badge'
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '@console/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@console/components/ui/table'

type Result={stage_attempt_id:string;evidence_artifact_id:string;proof:{input:{reference_url:string;binding_scope:string};
  reference:{video_id:string;chapters:{title:string;reported_start_ms:number}[]};comparison:{reference_token_count:number;asr_token_count:number;
  difference_count:number;differences:{kind:string;reference_text:string;asr_text:string;nearby_asr_chunks:number[];contains_number:boolean}[]}}}

export function SourceReference({projectId,runId,sourceId,status,canRun,identity}:{projectId:string;runId:string;sourceId:string|null;status:string;canRun:boolean;identity:string}){
  const [selected,setSelected]=useState(sourceId||''),[url,setUrl]=useState<string|null>(null),[limit,setLimit]=useState(10),[expanded,setExpanded]=useState(false)
  const key=useRef(crypto.randomUUID()),cache=useQueryClient()
  const sources=useQuery({queryKey:['deep-dub','reference-sources',identity,projectId],
    queryFn:()=>api<{items:{id:string;revision:number}[];has_more:boolean}>(`/apps/deep_dub/projects/${projectId}/records/sources?offset=0&limit=50`),enabled:!sourceId})
  const source=sourceId||selected
  const result=useQuery({queryKey:['deep-dub','source-reference',identity,projectId,source],enabled:!!source,
    queryFn:()=>api<Result|null>(`/apps/deep_dub/projects/${projectId}/sources/${source}/reference`),staleTime:30000})
  const referenceUrl=url??result.data?.proof.input.reference_url??''
  const compare=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/compare-source-reference`,{
    method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({source_id:source,reference_url:referenceUrl})}),
    onSuccess:async data=>{await cache.invalidateQueries({queryKey:['deep-dub']});toast.success(data.status==='succeeded'?'参考差异证据已保存':'阶段状态：'+data.status)},
    onError:error=>toast.error(error.message)})
  const item=result.data
  return <section className="border-t pt-5 mt-5 space-y-3" aria-label="官方参考与来源差异">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium">官方参考与来源差异</h3><Badge variant="outline">差异待复核</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">与耶鲁官方课程逐字稿比较，核对页面嵌入的视频 ID。差异不自动改稿，官方章节时间不作为字幕时间。本机视频与线上视频的一致性仍需核验。</p>
    {!sourceId&&<div className="space-y-2"><label className="text-xs" htmlFor="reference-source">选择本项目来源作诊断</label>
      <Select value={selected} onValueChange={value=>{setSelected(value);setUrl(null);setLimit(10);key.current=crypto.randomUUID()}}><SelectTrigger id="reference-source" aria-label="参考诊断来源"><SelectValue placeholder="选择来源版本"/></SelectTrigger><SelectContent>{sources.data?.items.map(s=><SelectItem key={s.id} value={s.id}>来源修订 {s.revision} · {s.id.slice(0,8)}</SelectItem>)}</SelectContent></Select>
      <p className="text-xs text-muted-foreground">未绑定来源的运行仅允许显式复用的验收诊断；不会绑定、替换本轮来源。</p>
      {sources.data?.has_more&&<p className="text-xs text-muted-foreground">此处显示最近 50 个来源，旧版本可通过 CLI 指定。</p>}
    </div>}
    {(sources.error||result.error)&&<p role="alert" className="text-xs text-destructive">{sources.error?.message||result.error?.message}<Button size="sm" variant="ghost" onClick={()=>{sources.refetch();result.refetch()}}>重新读取</Button></p>}
    {item&&<><p className="text-sm">{item.proof.comparison.difference_count} 处文本差异 · 其中 {item.proof.comparison.differences.filter(d=>d.contains_number).length} 处涉及数字</p>
      <p className="text-xs text-muted-foreground">官方参考 {item.proof.comparison.reference_token_count} 词 · ASR {item.proof.comparison.asr_token_count} 词 · {item.proof.reference.chapters.length} 个官方章节 · 内容与字幕时序未测</p>
      <details onToggle={e=>setExpanded(e.currentTarget.open)}><summary className="cursor-pointer text-xs">逐项查看参考与识别差异</summary>{expanded&&<>
        <Table><TableHeader><TableRow><TableHead>官方参考</TableHead><TableHead>ASR 候选</TableHead><TableHead>附近音轨块</TableHead></TableRow></TableHeader><TableBody>{item.proof.comparison.differences.slice(0,limit).map((d,i)=><TableRow key={i}><TableCell className="text-xs whitespace-pre-wrap align-top">{d.reference_text||'（参考无对应词）'}{d.contains_number&&<Badge variant="outline" className="ml-2">数字</Badge>}</TableCell><TableCell className="text-xs whitespace-pre-wrap align-top">{d.asr_text||'（ASR 无对应词）'}</TableCell><TableCell className="text-xs align-top">{d.nearby_asr_chunks.map(n=>n+1).join('、')}</TableCell></TableRow>)}</TableBody></Table>
        {limit<item.proof.comparison.differences.length&&<Button size="sm" variant="ghost" onClick={()=>setLimit(n=>n+10)}>再显示 10 项</Button>}
        <p className="text-xs font-mono text-muted-foreground break-all mt-3">证据 {item.evidence_artifact_id}</p>
        <p className="text-xs text-muted-foreground mt-2">附近音轨块从 1 开始编号，只用于找原音，不是精确词时间或字幕落点。表格两列都不自动判定为正确。</p>
      </>}</details></>}
    <div className="space-y-2"><label className="text-xs" htmlFor="official-reference-url">耶鲁官方课程页 URL</label><Input id="official-reference-url" value={referenceUrl} placeholder="https://oyc.yale.edu/history/hist-119/lecture-1" disabled={compare.isPending} onChange={e=>{setUrl(e.target.value);key.current=crypto.randomUUID()}}/>
      <Button size="sm" variant="outline" disabled={!canRun||status!=='running'||!source||!referenceUrl||compare.isPending} onClick={()=>compare.mutate()}>{compare.isPending?'正在获取并比对…':'获取官方参考并比对'}</Button>
      <p className="text-xs text-muted-foreground">执行需要运行、评分和设备权限。同输入复用已保存证据；原始 HTML 保存在私有对象库中。</p>
    </div>
  </section>
}
