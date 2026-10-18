import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@console/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@console/components/ui/table'

type Timing={source_id:string;stage_attempt_id:string;evidence_artifact_id:string;measurement:{model:{model_id:string};
  session:{requests:number;warm_cache_checks:number;operation_stopped:boolean};
  summary:{chunk_count:number;word_count:number;invalid_timestamp_chunks:number;uncovered_transcript_chunks:number;empty_asr_chunks:number;
    zero_length_content_words:number;cross_chunk_overlap_samples:number;scope:string};
  words:{word:string;chunk_index:number;source_start_sample:number;source_end_sample:number;reported_confidence:number|null}[]}}

export function SourceWordTiming({projectId,runId,sourceId,status,canRun,identity}:{projectId:string;runId:string;sourceId:string;status:string;canRun:boolean;identity:string}){
  const [confirm,setConfirm]=useState(false),[expanded,setExpanded]=useState(false),[limit,setLimit]=useState(30)
  const key=useRef(crypto.randomUUID()),cache=useQueryClient()
  const result=useQuery({queryKey:['deep-dub','source-word-timing',identity,projectId,sourceId],
    queryFn:()=>api<Timing|null>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/word-timing`),staleTime:30000})
  const execute=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/align-source`,{
    method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({source_id:sourceId,timeout_seconds:3600})}),
    onSuccess:async data=>{await cache.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success(data.status==='succeeded'?'来源词时间证据已保存':'阶段状态：'+data.status)},
    onError:error=>toast.error(error.message)})
  const measured=result.data?.measurement,summary=measured?.summary
  return <section className="border-t pt-5 mt-5 space-y-3" aria-label="完整来源词时间">
    <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-medium">完整来源词时间</h3><Badge variant="outline">ASR 文本候选 · 未终审</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">使用完整识别的原始 PCM 和原始候选文本，一个阶段复用同一对齐模型。测量只给出音轨样本坐标，不自动校正文案、建立语义段或生成字幕。</p>
    {result.error?<p role="alert" className="text-xs text-destructive">{result.error.message}<Button size="sm" variant="ghost" onClick={()=>result.refetch()}>重试读取</Button></p>
      :result.isLoading?<p className="text-xs text-muted-foreground">正在读取完整来源词时间…</p>
      :measured&&summary?<>
        <p className="text-sm">{summary.word_count} 个词边界 · {summary.chunk_count} 个来源块 · {measured.session.requests} 次模型请求</p>
        <p className="text-xs text-muted-foreground">异常时间块 {summary.invalid_timestamp_chunks} · 文本未覆盖块 {summary.uncovered_transcript_chunks} · 空文本块 {summary.empty_asr_chunks} · 零时长词 {summary.zero_length_content_words} · 跨块重叠 {summary.cross_chunk_overlap_samples} 样本</p>
        <p className="text-xs text-muted-foreground">{measured.session.warm_cache_checks} 次已加载检查 · 受控模型进程{measured.session.operation_stopped?'已退出':'未确认退出'} · {measured.model.model_id}</p>
        <details onToggle={e=>setExpanded(e.currentTarget.open)}><summary className="cursor-pointer text-xs">查看原始词边界候选</summary>{expanded&&<>
          <Table><TableHeader><TableRow><TableHead>ASR 词</TableHead><TableHead>音轨起点 / 秒</TableHead><TableHead>音轨终点 / 秒</TableHead><TableHead>来源块</TableHead></TableRow></TableHeader><TableBody>{measured.words.slice(0,limit).map((word,index)=><TableRow key={index}><TableCell className="text-xs">{word.word}</TableCell><TableCell className="text-xs font-mono">{(word.source_start_sample/16000).toFixed(4)}</TableCell><TableCell className="text-xs font-mono">{(word.source_end_sample/16000).toFixed(4)}</TableCell><TableCell className="text-xs">{word.chunk_index+1}</TableCell></TableRow>)}</TableBody></Table>
          {limit<measured.words.length&&<Button size="sm" variant="ghost" onClick={()=>setLimit(n=>n+30)}>再显示 30 个词</Button>}
          <p className="text-xs font-mono text-muted-foreground break-all mt-3">证据 {result.data?.evidence_artifact_id}</p>
        </>}</details>
      </>:<p className="text-xs text-muted-foreground">尚无成功的完整来源词时间证据。</p>}
    <p className="text-xs text-muted-foreground leading-6">原始越界、重叠或零时长值如实保留；内容、说话人、语义分段与字幕时钟映射仍需独立复核。不能把测量完成当作字幕验收通过。</p>
    <Button size="sm" variant="outline" disabled={!canRun||status!=='running'||execute.isPending} onClick={()=>setConfirm(true)}>测量完整来源词时间</Button>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>测量完整来源词时间</DialogTitle><DialogDescription>处理本来源全部已识别音轨块，使用受控 GPU 模型和设备租约，可能耗时数分钟。同输入重放成功回执；不会自动修正 ASR 错词或签发字幕审核。</DialogDescription></DialogHeader>
      <p className="text-xs text-muted-foreground">执行中可返回运行详情取消整个运行；设备在实际模型进程退出后释放。</p>
      <DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>{execute.isPending?'返回运行记录':'返回'}</Button><Button disabled={execute.isPending} onClick={()=>execute.mutate()}>{execute.isPending?'正在测量…':'确认测量'}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>
}
