import { ArrowRight, CheckCircle2, FileText, Headphones, ListChecks } from "lucide-react";
import { Button } from "@console/components/ui/button";
import { Badge } from "@console/components/ui/badge";
import { clock, type Segment, type Task } from "./model";

export type ReviewFilter = "all" | "issues" | "context" | "unmeasured" | "no-text" | "no-audio" | "drafts";
export function inReviewQueue(segment: Segment, filter: ReviewFilter, drafts: Record<string, string>) {
  switch (filter) {
    case "issues": return segment.quality?.decision === "failed";
    case "context": return !!segment.quality?.advisories?.length;
    case "unmeasured": return !["passed", "failed"].includes(segment.quality?.decision || "");
    case "no-text": return !segment.translation?.trim();
    case "no-audio": return !segment.tts;
    case "drafts": return drafts[segment.segmentId] !== undefined && drafts[segment.segmentId] !== (segment.translation || "");
    default: return true;
  }
}

export function ReviewOverview({ task, drafts, onQueue }: {
  task: Task; drafts: Record<string, string>; onQueue: (filter: ReviewFilter) => void;
}) {
  const count = (filter: ReviewFilter) => task.segments.filter(s => inReviewQueue(s, filter, drafts)).length;
  const total = task.segments.length;
  const text = total - count("no-text"), audio = total - count("no-audio");
  const queues: { key: ReviewFilter; title: string; note: string; tone: string }[] = [
    { key: "issues", title: "需要返修", note: "查看当前结构检查中的失败与诊断", tone: "bad" },
    { key: "unmeasured", title: "尚未验收", note: "没有明确通过或失败记录的语义段", tone: "pending" },
    { key: "context", title: "情境证据", note: "逐段核对提醒、取舍理由与适用版本", tone: "context" },
    { key: "drafts", title: "未保存草稿", note: "回到修改过的段落，逐段确认保存", tone: "draft" },
  ];
  return <div className="dd-review-overview">
    <div className="dd-overview-identity">
      <Badge variant="outline">{task.status}</Badge>
      <h2>{task.source.name}</h2>
      <p>{total} 个语义段 · {clock(task.source.durationSec)} · {task.qualityProfile?.label || "档位未登记"}</p>
      <code>{task.taskId}</code>
    </div>
    <section aria-label="制作准备" className="dd-overview-section">
      <h3>制作准备</h3>
      {[
        { label: "已保存译稿", value: text, icon: FileText, filter: "no-text" as ReviewFilter },
        { label: "已选配音", value: audio, icon: Headphones, filter: "no-audio" as ReviewFilter },
      ].map(({ label, value, icon: Icon, filter }) => <div key={label} className="dd-readiness">
        <div><span><Icon size={15} />{label}</span><strong>{value}<small> / {total}</small></strong></div>
        <div role="progressbar" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={total || 1} className="dd-readiness-bar"><i style={{ width: `${total ? value / total * 100 : 0}%` }} /></div>
        <Button variant="link" size="sm" disabled={value === total} onClick={() => onQueue(filter)}>{value === total ? "所有段均有记录" : `定位缺失的 ${total - value} 段`}<ArrowRight size={12} /></Button>
      </div>)}
      <p className="dd-small-note">记录齐全表示可继续审阅，内容准确性、音色与自然度仍需各自的验收证据。</p>
    </section>
    <section aria-label="审阅队列" className="dd-overview-section">
      <h3><ListChecks size={16} />审阅队列</h3>
      <div className="dd-review-queues">{queues.map(q => <Button key={q.key} variant="outline" className={`dd-review-queue ${q.tone}`} disabled={!count(q.key)} onClick={() => onQueue(q.key)}>
        <span><b>{q.title}</b><small>{q.note}</small></span><strong>{count(q.key)}</strong><ArrowRight size={15} />
      </Button>)}</div>
      <p className="dd-small-note">情境提醒可与其他状态重叠；这些数字不能相加作为验收通过率。队列导航只改变审阅范围。</p>
    </section>
    <div className="dd-overview-next"><CheckCircle2 size={19} /><p>结构通过后，继续核对当前版本的内容、听感和成片。<br />未测项目会保留为未测。</p></div>
  </div>;
}
