export type Wave = { durationSec: number; peaks: number[] };
export type Score = {
  total?: number;
  decision?: string;
  durationScore?: number;
  boundaryScore?: number;
  envelopeScore?: number;
  activeSpanRatio?: number;
  onsetDeltaSec?: number;
  offsetDeltaSec?: number;
  weights?: Record<string, number>;
  method?: string;
  semanticReview?: string;
  listening?: string;
};
export type Translation = {
  revision: number;
  translation: string;
  selected: boolean;
  sources?: { model?: string; overallScore?: number }[];
};
export type AudioVersion = {
  attempt: number;
  selected: boolean;
  durationSec?: number;
  provider?: string;
  translation?: string;
  translationRevision?: number;
  score?: Score;
  review?: string;
  findings?: string[];
  emotionMode?: string;
};
export type Segment = {
  emotionReady?: boolean;
  segmentId: string;
  speakerId: string;
  startSec: number;
  endSec: number;
  playbackStartSec: number;
  sourceText: string;
  translation?: string;
  translationCandidate?: string;
  translationCandidateFlags?: string[];
  tts?: { attempt: number; durationSec: number };
  versions?: { translations: Translation[]; audio: AudioVersion[] };
  quality?: {
    decision?: string;
    issues?: string[];
    advisories?: string[];
    contextualAcceptance?: { reason: string; evidence: string };
    metrics?: {
      alignment?: {
        onsetDeltaSec?: number;
        offsetDeltaSec?: number;
        repairPlan?: string[];
      };
    };
  };
};
export type Voice = {
  revision: number;
  voiceMode?: string;
  identityReview?: string;
  selectionScope?: string;
  sourceVoiceWaveform?: Wave;
  voiceWaveform?: Wave;
  sourceVoiceProfile?: Record<string, number>;
  voiceProfile?: Record<string, number>;
  processing?: Record<string, unknown>;
};
export type Strategy = {
  id: string;
  label: string;
  audio?: string;
  waveform?: Wave;
  overallScore?: number;
  contentScore?: number;
  timingScore?: number;
  coverage?: number;
  audioCount: number;
  segments: number;
  timingPassed: number;
  listening?: string;
};
export type Task = {
  sourceReady?: boolean;
  taskId: string;
  status: string;
  source: { name: string; path?: string; durationSec: number };
  render?: unknown;
  renderPath?: string;
  renderCacheCount?: number;
  segments: Segment[];
  speakerNames?: Record<string, string>;
  voices?: Record<string, Voice>;
  background?: { gain?: number; rawAvailable?: boolean };
  production?: {
    measured?: boolean;
    complete?: boolean;
    rtfUnrounded?: number;
    [key: string]: unknown;
  };
  qualityProfile?: { id: string; label: string };
  qualityCatalog?: {
    profiles: Record<string, { label: string; description: string }>;
    commonRequirements: string[];
  };
  latestValidation?: {
    decision?: string;
    at?: string;
    qualityProfile?: { label?: string };
  };
  strategyComparison?: { method: string; strategies: Strategy[] };
};
export const clock = (value: number) => {
  const n = Math.max(0, Number(value) || 0);
  return `${Math.floor(n / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(n % 60)
    .toString()
    .padStart(2, "0")}`;
};
export const precise = (n: number) =>
  `${clock(n)}.${Math.floor((n % 1) * 100)
    .toString()
    .padStart(2, "0")}`;
export const signed = (n?: number) =>
  n == null ? "未测" : `${n > 0 ? "+" : ""}${n.toFixed(2)}s`;
export function decision(s: Segment) {
  return s.quality?.decision === "failed"
    ? "待返修"
    : s.quality?.decision === "passed"
      ? s.quality.advisories?.length
        ? "情境通过"
        : "结构通过"
      : "待验收";
}
export interface TaskAdapter {
  reconnectMedia?(): Promise<unknown>;
  cacheKey?: string;
  canWrite?(action:string):boolean;
  read(): Promise<Task>;
  wave(segment: string, kind: string): Promise<Wave>;
  media(kind: string, query?: Record<string, string | number>): string;
  write(action: string, body: unknown): Promise<unknown>;
  job(id: string): Promise<{ state: string; error?: string }>;
}
export function localReviewAdapter(taskId: string): TaskAdapter {
  const root = `/__deep_dub_review/task/${encodeURIComponent(taskId)}`;
  async function request<T>(path: string, body?: unknown): Promise<T> {
    const r = await fetch(
      root + path,
      body === undefined
        ? undefined
        : {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Deep-Dub-UI": "local-review",
            },
            body: JSON.stringify(body),
          },
    );
    const data = await r.json();
    if (!r.ok) throw Error(data.error || `请求失败 ${r.status}`);
    return data;
  }
  return {
    read: () => request("/api/task"),
    wave: (segment, kind) =>
      request("/api/waveform?" + new URLSearchParams({ segment, kind })),
    media: (kind, query = {}) =>
      root +
      "/media/" +
      kind +
      "?" +
      new URLSearchParams(
        Object.entries(query).map(([k, v]) => [k, String(v)]),
      ),
    write: (action, body) => request("/api/" + action, body),
    job: (id) => request("/api/job/" + encodeURIComponent(id)),
  };
}
