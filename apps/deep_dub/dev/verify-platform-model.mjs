import assert from 'node:assert/strict'
import {normalizeSnapshot} from '../ui/review/platform-model.ts'

// 纯视图契约夹具：不能用它证明媒体质量或实际生产。
const raw={schema_version:1,project:{id:'project',name:'单元验收'},source:{id:'source',duration_ms:2000},segment_set:{id:'parts'},
  quality_profile:{id:'profile',profile_key:'high',revision:1},timeline:{id:'timeline'},
  segments:[{id:'segment',segment_no:0,speaker_id:'speaker',start_ms:0,end_ms:1000,source_text:'Unit fixture.'}],
  translations:[{id:'text1',segment_id:'segment',revision:1,chinese_text:'旧稿。',model_id:'fixture-model'}],translation_edits:[],
  audio_attempts:[{id:'audio1',segment_id:'segment',translation_id:'text1',artifact_id:'wav1',duration_ms:900,parameters:{model_id:'fixture-tts'}}],
  speakers:[{id:'speaker',display_name:'单元角色'}],voice_references:[],emotion_references:[],
  selections:[{id:'selection1',segment_id:'segment',translation_id:'text1',audio_attempt_id:'audio1',revision:1}],
  timeline_clips:[{id:'clip1',segment_id:'segment',selection_id:'selection1',start_ms:0}],backgrounds:[],
  renders:[{id:'render1',revision:1,strategy_key:'fixture',mix_artifact_id:'mix',video_artifact_id:'video'}],audits:[],artifacts:[],runs:[]}
let view=normalizeSnapshot(raw)
assert.equal(view.segments[0].quality.decision,'passed')
assert.equal(view.segments[0].tts.attempt,1)
assert.ok(view.render)
raw.translations.push({id:'text2',segment_id:'segment',revision:2,chinese_text:'人工新稿。',model_id:'human:editor'})
view=normalizeSnapshot(raw)
assert.equal(view.segments[0].translation,'人工新稿。')
assert.equal(view.segments[0].quality.decision,'unmeasured')
assert.equal(view.segments[0].tts,undefined)
assert.equal(view.render,undefined)
assert.equal(view.segments[0].versions.audio.length,1)
assert.equal(view.segments[0].versions.audio[0].translation,'旧稿。')
assert.equal(view.renderCacheCount,1)
raw.audio_attempts.push({id:'audio2',segment_id:'segment',translation_id:'text2',artifact_id:'wav2',duration_ms:950,parameters:{model_id:'fixture-tts'}})
view=normalizeSnapshot(raw)
assert.equal(view.segments[0].tts.attempt,2)
assert.equal(view.segments[0].quality.decision,'unmeasured')
assert.equal(view.render,undefined)
assert.equal(view.production.measured,false)
assert.equal(view.strategyComparison.strategies[0].listening,'未测')
assert.equal(view.segments[0].versions.audio[1].score,undefined)
console.log(JSON.stringify({passed:true,scope:'新稿不沿用旧音频/审核/成片；候选不自动批准；不伪造得分/RTF',checks:16}))
