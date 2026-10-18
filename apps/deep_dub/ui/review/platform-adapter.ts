import {api} from '@console/lib/api'
import type {TaskAdapter} from './model'
import {normalizeSnapshot,segmentLabel,text,type StudioSnapshot} from './platform-model'

export function platformAdapter(projectId:string,identity:string,permissions:readonly string[]):TaskAdapter {
  let snapshot:StudioSnapshot|undefined
  const keys=new Map<string,string>()
  const canWrite=(action:string)=>action==='translation'&&permissions.includes('deep_dub.project.edit')
  const file=(id:unknown)=>id?'/api/apps/deep_dub/media/artifacts/'+encodeURIComponent(String(id)):''
  return {cacheKey:identity+':'+projectId,canWrite,
    async read(){snapshot=await api<StudioSnapshot>(`/apps/deep_dub/projects/${projectId}/studio`);return normalizeSnapshot(snapshot)},
    async wave(label,kind){
      const raw=snapshot,segment=raw?.segments.find(s=>segmentLabel(s)===label)
      if(!raw||!segment)throw Error('段不属于当前快照')
      let artifact=raw.source?.artifact_id,start=Number(segment.start_ms)/1000,end=Number(segment.end_ms)/1000
      if(kind==='tts'||kind.startsWith('tts-')){
        const audio=raw.audio_attempts.filter(a=>a.segment_id===segment.id)
        const current=normalizeSnapshot(raw).segments.find(s=>s.segmentId===label)?.tts?.attempt
        const selected=audio[(kind==='tts'?(current||0):Number(kind.slice(4)))-1]
        if(!selected)throw Error('当前声音版本不存在')
        artifact=selected.artifact_id;start=0;end=Number(selected.duration_ms)/1000
      }
      if(!artifact)throw Error('此版本尚未登记音频产物')
      return api(`/apps/deep_dub/media/artifacts/${artifact}/waveform?`+new URLSearchParams({start:String(start),end:String(end),points:'640'}))
    },
    media(kind,query={}){
      const raw=snapshot;if(!raw)return ''
      const segment=raw.segments.find(s=>segmentLabel(s)===query.segment)
      if(kind==='preview'||kind==='source')return file(raw.source?.artifact_id)
      if(kind==='emotion')return file(raw.emotion_references.filter(e=>e.segment_id===segment?.id).at(-1)?.original_artifact_id)
      if(kind==='tts')return file(raw.audio_attempts.filter(a=>a.segment_id===segment?.id)[Number(query.attempt)-1]?.artifact_id)
      if(kind==='background'){const b=raw.backgrounds.at(-1);return file(query.kind==='raw'?b?.original_artifact_id:b?.processed_artifact_id)}
      if(kind==='voice'){const v=raw.voice_references.filter(v=>v.speaker_id===query.speaker).at(-1);return file(query.kind==='sourceVoice'?v?.original_artifact_id:v?.processed_artifact_id)}
      if(kind==='render')return file(raw.renders.at(-1)?.video_artifact_id)
      if(kind==='strategy')return file(raw.renders.find(r=>r.id===query.strategy)?.mix_artifact_id)
      return ''
    },
    async write(action,input){
      if(!canWrite(action))throw Error('此操作的业务执行器尚未接入或当前用户无权执行')
      const raw=snapshot,body=input as Record<string,unknown>
      const segment=raw?.segments.find(s=>segmentLabel(s)===body.segmentId)
      if(!raw||!segment)throw Error('段不属于当前快照，请重新加载')
      const latest=raw.translations.filter(t=>t.segment_id===segment.id).at(-1)
      const payload={base_translation_id:latest?.id||null,expected_revision:latest?Number(latest.revision):0,
        expected_project_version:Number(raw.project.row_version),chinese_text:text(body.translation)}
      const fingerprint=JSON.stringify([segment.id,payload]);if(!keys.has(fingerprint))keys.set(fingerprint,crypto.randomUUID())
      return api(`/apps/deep_dub/projects/${projectId}/segments/${segment.id}/translations`,{method:'POST',
        headers:{'Idempotency-Key':keys.get(fingerprint)!},body:JSON.stringify(payload)})
    },
    async job(){throw Error('当前平台配音作业执行器尚未接入')}}
}
