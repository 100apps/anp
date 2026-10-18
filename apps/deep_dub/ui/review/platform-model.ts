import type { Task,Segment,Voice } from './model'

export type Row=Record<string,unknown>&{id:string}
export type StudioSnapshot={schema_version:1;project:Row;source:Row|null;segment_set:Row|null;quality_profile:Row|null;timeline:Row|null;
  segments:Row[];translations:Row[];translation_edits:Row[];audio_attempts:Row[];speakers:Row[];voice_references:Row[];
  emotion_references:Row[];selections:Row[];timeline_clips:Row[];backgrounds:Row[];renders:Row[];audits:Row[];artifacts:Row[];runs:Row[]}
export const text=(value:unknown)=>typeof value==='string'?value:''
const number=(value:unknown)=>Number.isFinite(Number(value))?Number(value):0
const byRevision=(rows:Row[])=>[...rows].sort((a,b)=>number(a.revision)-number(b.revision))
const last=(rows:Row[])=>rows.at(-1)
export const segmentLabel=(row:Row)=>'seg'+String(number(row.segment_no)+1).padStart(4,'0')
export function normalizeSnapshot(raw:StudioSnapshot):Task {
  if(raw.schema_version!==1)throw Error('工作台数据契约版本不支持')
  const voices:Record<string,Voice>={}
  for(const speaker of raw.speakers){const voice=last(byRevision(raw.voice_references.filter(v=>v.speaker_id===speaker.id)));if(voice)voices[speaker.id]={revision:number(voice.revision),selectionScope:'episode',voiceMode:'本集固定音色',identityReview:'未测',processing:voice.processing as Record<string,unknown>}}
  const clips=new Map(raw.timeline_clips.map(c=>[text(c.segment_id),c]))
  const segments:Segment[]=raw.segments.map(row=>{
    const translations=byRevision(raw.translations.filter(t=>t.segment_id===row.id)),working=last(translations)
    const audio=raw.audio_attempts.filter(a=>a.segment_id===row.id)
    const selection=clips.has(row.id)?raw.selections.find(s=>s.id===clips.get(row.id)?.selection_id):last(byRevision(raw.selections.filter(s=>s.segment_id===row.id)))
    // 最新人工/模型稿是工作稿；旧批准组合完整保留，但不能让它的音频成为新稿的“当前已通过”。
    const approved=!!working&&selection?.translation_id===working.id?audio.find(a=>a.id===selection.audio_attempt_id):undefined
    const current=approved||last(audio.filter(a=>a.translation_id===working?.id))
    const approvedCurrent=!!approved&&approved.id===current?.id
    return {segmentId:segmentLabel(row),speakerId:text(row.speaker_id),startSec:number(row.start_ms)/1000,endSec:number(row.end_ms)/1000,
      playbackStartSec:clips.has(row.id)&&approvedCurrent?number(clips.get(row.id)?.start_ms)/1000:number(row.start_ms)/1000,
      sourceText:text(row.source_text),translation:working?text(working.chinese_text):undefined,
      emotionReady:raw.emotion_references.some(e=>e.segment_id===row.id),
      tts:current?{attempt:audio.indexOf(current)+1,durationSec:number(current.duration_ms)/1000}:undefined,
      quality:{decision:approvedCurrent?'passed':'unmeasured',advisories:approvedCurrent?[]:undefined},
      versions:{translations:translations.map(t=>({revision:number(t.revision),translation:text(t.chinese_text),selected:t.id===working?.id,sources:[{model:text(t.model_id)}]})),
        audio:audio.map((a,i)=>{const translation=translations.find(t=>t.id===a.translation_id),parameters=a.parameters as Record<string,unknown>|undefined;
          return {attempt:i+1,selected:a.id===current?.id,durationSec:number(a.duration_ms)/1000,provider:text(parameters?.model_id)||'模型未登记',
            translation:text(translation?.chinese_text),translationRevision:translation?number(translation.revision):undefined,
            review:a.id===approved?.id?'绑定当前选版审核':'候选，须独立复核',emotionMode:'segment-source'}})}}
  })
  const currentCombination=!!raw.timeline&&segments.length>0&&raw.timeline_clips.length===segments.length&&raw.segments.every(s=>{
    const clip=clips.get(s.id),selection=raw.selections.find(x=>x.id===clip?.selection_id),working=last(byRevision(raw.translations.filter(t=>t.segment_id===s.id)))
    return !!selection&&!!working&&selection.translation_id===working.id
  })
  const run=last(raw.runs),render=last(byRevision(raw.renders))
  const profile=raw.quality_profile,profileKey=text(profile?.profile_key)
  return {taskId:raw.project.id,status:run?({queued:'排队中',running:'执行中',waiting_review:'待审核',cancelled:'已取消',failed:'失败',succeeded:'技术验收完成'}[text(run.status)]||text(run.status)):'待准备',
    source:{name:text(raw.project.name),durationSec:number(raw.source?.duration_ms)/1000},sourceReady:!!raw.source,
    segments,speakerNames:Object.fromEntries(raw.speakers.map(s=>[s.id,text(s.display_name)||text(s.speaker_key)])),voices,
    qualityProfile:profile?{id:profileKey,label:({high:'高质量',medium:'中质量',low:'低质量'}[profileKey]||profileKey)+` · 修订 ${number(profile.revision)}`} :undefined,
    render:currentCombination?render:undefined,renderCacheCount:raw.renders.length,
    background:raw.backgrounds.length?{rawAvailable:true,gain:1}:undefined,
    strategyComparison:{method:'所选封存时间线的历史成片；工作稿变化不会改写旧输入与审核。工程得分未登记时保持未测',
      strategies:byRevision(raw.renders).map(r=>({id:r.id,label:`成片 v${number(r.revision)} · ${text(r.strategy_key)}`,audio:text(r.mix_artifact_id),audioCount:raw.timeline_clips.length,segments:segments.length,timingPassed:0,listening:'未测'}))},
    production:{measured:false,complete:!!run?.technical_verified_at}}
}
