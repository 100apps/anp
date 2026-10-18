import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  ExternalLink,
  FileText,
  Headphones,
  History,
  Info,
  Keyboard,
  ListFilter,
  LoaderCircle,
  Maximize2,
  Mic2,
  Minus,
  Music2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Repeat2,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  SkipBack,
  SkipForward,
  SlidersHorizontal,
  Sparkles,
  Volume2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { stopAuditions } from "./audition";
import { Button } from "@console/components/ui/button";
import { Badge } from "@console/components/ui/badge";
import { Input } from "@console/components/ui/input";
import { Textarea } from "@console/components/ui/textarea";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@console/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@console/components/ui/dialog";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@console/components/ui/sheet";
import {
  clock,
  precise,
  signed,
  decision,
  type Task,
  type Segment,
  type TaskAdapter,
  type Score,
} from "./model";
import { TimelineClip } from "./timeline-clip";
import { useTransport } from "./transport";
import { WavePlayer } from "./wave-player";

function QualityBadge({ segment }: { segment: Segment }) {
  return (
    <span
      className={
        "dd-status " +
        (segment.quality?.decision === "failed"
          ? "bad"
          : segment.quality?.decision === "passed"
            ? "good"
            : "pending")
      }
    >
      <i />
      {decision(segment)}
    </span>
  );
}
function ScoreCard({ score }: { score?: Score }) {
  return (
    <div className="dd-score">
      <div className="dd-section-title">
        <span>
          时序代理 <small>非音质评分</small>
        </span>
        <strong>{score?.total?.toFixed(1) ?? "未测"}</strong>
      </div>
      <div className="dd-metrics">
        {[
          ["时长", score?.durationScore],
          ["首尾", score?.boundaryScore],
          ["包络", score?.envelopeScore],
        ].map(([k, v]) => (
          <div key={k}>
            <span>{k}</span>
            <b>{typeof v === "number" ? v.toFixed(1) : "未测"}</b>
          </div>
        ))}
      </div>
      <p>
        起点 {signed(score?.onsetDeltaSec)} · 终点{" "}
        {signed(score?.offsetDeltaSec)}
      </p>
      <p>
        权重：时长 {score?.weights?.duration ?? "—"} / 首尾{" "}
        {score?.weights?.boundary ?? "—"} / 包络{" "}
        {score?.weights?.envelope ?? "—"}
      </p>
      <p>{score?.method || "暂无当前版本的评分记录"}</p>
      <p>
        语义：{score?.semanticReview || "须独立复核"} · 听感：
        {score?.listening || "未测"}
      </p>
    </div>
  );
}

export function TaskStudio({
  adapter,
  taskId,
  backHref = 'http://127.0.0.1:8765/',
}: {
  adapter: TaskAdapter;
  taskId: string;
  backHref?: string;
}) {
  const query = useQuery({
    queryKey: ["dd-review-task", adapter.cacheKey || taskId],
    queryFn: () => adapter.read(),
  });
  const task = query.data;
  const can=(action:string)=>adapter.canWrite?.(action)??true;
  const initial = new URLSearchParams(location.search).get("segment");
  const [selectedId, setSelectedId] = useState(initial || ""),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [editorTab, setEditorTab] = useState("edit");
  const [drafts, setDrafts] = useState<Record<string, string>>({}),
    [placement, setPlacement] = useState(""),
    [factor, setFactor] = useState("1.00");
  const [captions, setCaptions] = useState(false),
    [panel, setPanel] = useState<
      "voices" | "quality" | "strategies" | "help" | null
    >(null),
    [mobilePane, setMobilePane] = useState("preview");
  const [confirm, setConfirm] = useState<{
      title: string;
      description: string;
      action: () => Promise<unknown>;
    } | null>(null),
    [busy, setBusy] = useState(false);
  const [job, setJob] = useState<{
    id: string;
    segment: string;
    state: string;
    error?: string;
  } | null>(null);
  const [quality, setQuality] = useState(""),
    [zoom, setZoom] = useState(1),
    [viewStart, setViewStart] = useState(0);
  const timeline = useRef<HTMLDivElement>(null),
    rail = useRef<HTMLDivElement>(null),
    editorBody = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (editorBody.current) editorBody.current.scrollTop = 0;
  }, [editorTab]);
  const segment =
    task?.segments.find((s) => s.segmentId === selectedId) || task?.segments[0];
  const transport = useTransport(task, adapter, segment);
  const draft = segment
    ? (drafts[segment.segmentId] ?? segment.translation ?? "")
    : "";
  const dirty = !!segment && draft !== (segment.translation || "");
  const dirtyCount =
    task?.segments.filter(
      (s) =>
        drafts[s.segmentId] !== undefined &&
        drafts[s.segmentId] !== s.translation,
    ).length || 0;
  const filtered = useMemo(
    () =>
      task?.segments.filter(
        (s) =>
          (filter === "all" ||
            (filter === "issues" && s.quality?.decision === "failed") ||
            (filter === "unmeasured" && s.quality?.decision !== "passed") ||
            (filter === "context" && !!s.quality?.advisories?.length)) &&
          `${s.segmentId} ${s.sourceText} ${s.translation || ""} ${task.speakerNames?.[s.speakerId] || s.speakerId}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ) || [],
    [task, search, filter],
  );
  useEffect(() => {
    if (segment) {
      setPlacement(segment.playbackStartSec.toFixed(2));
      setFactor("1.00");
      const url = new URL(location.href);
      url.searchParams.set("segment", segment.segmentId);
      history.replaceState(null, "", url);
    }
  }, [segment?.segmentId, segment?.playbackStartSec]);
  useEffect(() => {
    if (!dirtyCount) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirtyCount]);
  const actions = useRef({ transport, segment, task });
  actions.current = { transport, segment, task };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (
        (e.target as HTMLElement).closest(
          'input,textarea,select,button,[contenteditable],[role="dialog"]',
        )
      )
        return;
      const { transport: t, segment: s, task: current } = actions.current;
      if (e.code === "Space") {
        e.preventDefault();
        t.toggle();
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        t.seek(t.position - 5);
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        t.seek(t.position + 5);
      }
      if ((e.key === "j" || e.key === "k") && current && s) {
        const i = current.segments.indexOf(s) + (e.key === "j" ? 1 : -1),
          next = current.segments[i];
        if (next) {
          t.pause();
          setSelectedId(next.segmentId);
          t.seek(next.startSec);
        }
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    if (!transport.follow || !transport.playing || !task) return;
    const active = task.segments.find(
      (s) => transport.position >= s.startSec && transport.position < s.endSec,
    );
    if (active && active.segmentId !== segment?.segmentId)
      setSelectedId(active.segmentId);
  }, [
    transport.position,
    transport.follow,
    transport.playing,
    task,
    segment?.segmentId,
  ]);
  useEffect(() => {
    rail.current
      ?.querySelector('[aria-current="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [segment?.segmentId]);
  useEffect(() => {
    if (!job || job.state !== "running") return;
    let disposed = false;
    const timer = setTimeout(async () => {
      try {
        const status = await adapter.job(job.id);
        if (disposed) return;
        setJob({ ...job, ...status });
        if (status.state === "done") {
          await query.refetch();
          toast.success("新配音已生成；请选择版本并复核");
        } else if (status.state !== "running")
          toast.error(status.error || "配音未完成");
      } catch (e) {
        if (!disposed)
          setJob({ ...job, state: "poll_error", error: (e as Error).message });
      }
    }, 1500);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [job, adapter, query.refetch]);
  function choose(s: Segment) {
    transport.pause();
    setSelectedId(s.segmentId);
    transport.seek(s.startSec);
  }
  function navigate(direction: number) {
    if (!task || !segment) return;
    const next = task.segments[task.segments.indexOf(segment) + direction];
    if (next) choose(next);
  }
  async function write(action: string, body: unknown) {
    if(!can(action))throw Error('当前用户无权执行或此业务执行器尚未接入');
    transport.pause();
    const result = await adapter.write(action, body);
    await query.refetch();
    return result;
  }
  function ask(
    title: string,
    description: string,
    action: () => Promise<unknown>,
  ) {
    if (
      ["保存译稿新版本", "更新配音落点", "更新说话人显示名"].includes(title)
    ) {
      if (busy) return;
      setBusy(true);
      void action()
        .then(() => toast.success(title + "已完成"))
        .catch((error: Error) => toast.error(error.message))
        .finally(() => setBusy(false));
      return;
    }
    setConfirm({ title, description, action });
  }
  async function apply() {
    if (!confirm) return;
    setBusy(true);
    try {
      await confirm.action();
      setConfirm(null);
      toast.success("操作已完成，历史版本保留");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function focusSegment() {
    if (!segment || !task) return;
    const span = Math.max(8, segment.endSec - segment.startSec);
    setZoom(Math.min(100, task.source.durationSec / (span * 2)));
    setViewStart(Math.max(0, segment.startSec - span * 0.35));
  }
  function zoomTo(next: number) {
    setZoom(next);
    setViewStart(
      Math.max(
        0,
        transport.position - ((task?.source.durationSec || 1) / next) * 0.4,
      ),
    );
  }
  useEffect(() => {
    const el = timeline.current;
    if (!el || !task) return;
    el.scrollLeft =
      (viewStart / task.source.durationSec) * Math.max(0, el.scrollWidth - 164);
  }, [zoom, viewStart, task?.source.durationSec]);
  useEffect(() => {
    const el = timeline.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      if (e.ctrlKey) {
        e.preventDefault();
        const rect = el.getBoundingClientRect(),
          fraction = Math.max(
            0,
            (e.clientX - rect.left - 164) / Math.max(1, el.clientWidth - 164),
          );
        const total = task?.source.durationSec || 1,
          anchor =
            ((el.scrollLeft + fraction * (el.clientWidth - 164)) /
              Math.max(1, el.scrollWidth - 164)) *
            total;
        setZoom((v) =>
          Math.max(1, Math.min(100, v * Math.exp(-e.deltaY * 0.005))),
        );
        setViewStart(
          Math.max(
            0,
            anchor -
              (fraction * total) /
                Math.max(1, zoom * Math.exp(-e.deltaY * 0.005)),
          ),
        );
      } else if (e.shiftKey) {
        e.preventDefault();
        el.scrollLeft += e.deltaY || e.deltaX;
      }
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [task?.source.durationSec, zoom]);
  if (!task)
    return (
      <div className="dd-studio dd-loading">
        {query.isError ? (
          <>
            <AudioLines size={40} />
            <h1>暂时无法载入任务</h1>
            <p>{query.error.message}</p>
            <Button onClick={() => query.refetch()}>
              <RefreshCw />
              重新连接
            </Button>
          </>
        ) : (
          <>
            <LoaderCircle className="animate-spin" />
            <p>正在载入真实任务与历史版本…</p>
          </>
        )}
      </div>
    );
  const total = task.source.durationSec,
    index = segment ? task.segments.indexOf(segment) : 0;
  const issues = task.segments.filter(
      (s) => s.quality?.decision === "failed",
    ).length,
    context = task.segments.filter((s) => s.quality?.advisories?.length).length;
  const speakerName = (id: string) => task.speakerNames?.[id] || id;
  return (
    <div className="dd-studio" data-testid="deep-dub-task-studio">
      <header className="dd-header">
        <a
          className="dd-brand"
          href={backHref}
          title="返回现有任务列表"
        >
          <span>
            <AudioLines size={22} />
          </span>
          <b>
            deep<span>dub</span>
            <small>译配制作台</small>
          </b>
        </a>
        <div className="dd-header-divider" />
        <div className="dd-task-title">
          <div>
            <span>任务工作区</span>
            <ChevronRight size={12} />
            <span>本机任务</span>
          </div>
          <h1 title={task.source.name}>{task.source.name}</h1>
        </div>
        <Badge className="dd-top-status" variant="outline">
          <i />
          {task.status}
        </Badge>
        <div className="dd-header-actions">
          <Button
            variant="ghost"
            size="icon"
            title="刷新任务"
            aria-label="刷新任务"
            onClick={() => query.refetch()}
          >
            <RefreshCw className={query.isFetching ? "animate-spin" : ""} />
          </Button>
          <Button variant="outline" onClick={() => setPanel("quality")}>
            <ShieldCheck />
            质量与交付
          </Button>
          <Button onClick={() => setPanel("strategies")}>
            <Headphones />
            成片试听
            <ArrowRight size={14} />
          </Button>
        </div>
      </header>
      <div className="dd-subbar">
        <div className="dd-tabs">
          <button className="active">
            <SlidersHorizontal size={14} />
            译配编辑
          </button>
          <button onClick={() => setPanel("voices")}>
            <Mic2 size={14} />
            说话人与音色
          </button>
          <button onClick={() => setPanel("strategies")}>
            <AudioLines size={14} />
            策略对比
          </button>
        </div>
        <div className="dd-task-meta">
          <span>{task.segments.length} 段</span>
          <i />
          <span>{clock(total)}</span>
          <i />
          <span>{task.qualityProfile?.label || "档位未登记"}</span>
          <button onClick={() => setPanel("help")} title="快捷键">
            <Keyboard size={15} />
          </button>
        </div>
      </div>
      <div className="dd-mobile-nav">
        {[
          ["segments", "分段"],
          ["preview", "画面与时间线"],
          ["editor", "当前段"],
        ].map(([k, v]) => (
          <Button
            key={k}
            variant={mobilePane === k ? "secondary" : "ghost"}
            size="sm"
            onClick={() => setMobilePane(k)}
          >
            {v}
          </Button>
        ))}
      </div>
      {query.isError && (
        <p className="dd-error" role="alert">
          刷新失败，当前显示上次快照。{query.error.message}
        </p>
      )}
      <main className="dd-workspace" data-pane={mobilePane}>
        <aside className="dd-segments" aria-label="分段导航">
          <div className="dd-panel-heading">
            <h2>
              <FileText size={15} />
              语义分段
            </h2>
            <span>
              {filtered.length} / {task.segments.length}
            </span>
          </div>
          <div className="dd-segment-tools">
            <div className="dd-search">
              <Search size={14} />
              <Input
                aria-label="搜索分段"
                placeholder="搜索原文、译文、段号…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search && (
                <button aria-label="清除搜索" onClick={() => setSearch("")}>
                  <X size={12} />
                </button>
              )}
            </div>
            <div className="dd-filters">
              {[
                ["all", "全部", task.segments.length],
                ["issues", "返修", issues],
                ["context", "情境", context],
                [
                  "unmeasured",
                  "未通过",
                  task.segments.filter((s) => s.quality?.decision !== "passed")
                    .length,
                ],
              ].map(([key, label, count]) => (
                <button
                  key={key}
                  className={filter === key ? "active" : ""}
                  onClick={() => setFilter(String(key))}
                >
                  {label}
                  <span>{count}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="dd-segment-list" ref={rail}>
            {filtered.length ? (
              filtered.map((s) => (
                <button
                  key={s.segmentId}
                  className={
                    "dd-segment " +
                    (segment?.segmentId === s.segmentId ? "selected" : "")
                  }
                  aria-current={segment?.segmentId === s.segmentId}
                  onClick={() => {
                    choose(s);
                    setMobilePane("editor");
                  }}
                >
                  <div className="dd-segment-top">
                    <span className="dd-mono">
                      {s.segmentId.replace("seg", "")}
                    </span>
                    <span className="dd-segment-time">
                      {clock(s.startSec)} — {clock(s.endSec)}
                    </span>
                    {drafts[s.segmentId] !== undefined &&
                      drafts[s.segmentId] !== s.translation && (
                        <i title="未保存草稿" className="dd-dirty-dot" />
                      )}
                  </div>
                  <p>
                    {s.translation || s.translationCandidate || s.sourceText}
                  </p>
                  <div className="dd-segment-bottom">
                    <span>{speakerName(s.speakerId)}</span>
                    <QualityBadge segment={s} />
                  </div>
                </button>
              ))
            ) : (
              <div className="dd-empty">
                <Search size={25} />
                <p>没有匹配的分段</p>
                <Button
                  variant="link"
                  onClick={() => {
                    setSearch("");
                    setFilter("all");
                  }}
                >
                  清除筛选
                </Button>
              </div>
            )}
          </div>
          <footer className="dd-rail-footer">
            <span>
              <i className="dd-live-dot" />
              本机任务 · 原始记录
            </span>
            <span>
              {dirtyCount ? `${dirtyCount} 段草稿未保存` : "所有编辑已保存"}
            </span>
          </footer>
        </aside>
        <section className="dd-center" aria-label="画面与多轨试听">
          <div className="dd-preview-header">
            <span>
              <i className="dd-live-dot" />
              同步画面
            </span>
            <span>画面静音 · 声音由监听轨混合</span>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label="全屏画面"
              onClick={() => transport.video.current?.requestFullscreen()}
            >
              <Maximize2 />
            </Button>
          </div>
          <div className="dd-preview">
            {task.sourceReady===false?<div className="dd-empty">尚未登记来源素材</div>:<video
              ref={transport.video}
              src={adapter.media("preview")}
              muted
              playsInline
              preload="metadata"
              onLoadedMetadata={(e) => {
                const wanted = Number(e.currentTarget.dataset.wanted);
                if (Number.isFinite(wanted))
                  e.currentTarget.currentTime = wanted;
                else transport.seek(task.segments[0]?.startSec || 0);
              }}
              onPlay={() => transport.setPlaying(true)}
              onPause={() => {
                transport.setPlaying(false);
              }}
              onEnded={() => transport.pause()}
              onError={() =>
                toast.error("画面无法加载，音频与任务数据仍可检查")
              }
              onClick={transport.toggle}
            />}
            <span className="dd-preview-label">
              {adapter.canWrite ? "原片预览 · 配音由监听轨混合" : task.render ? "已有技术成片" : "成片待生成"}
            </span>
            <span className="dd-preview-time dd-mono">
              {clock(transport.position)}
            </span>
            {captions && (
              <div className="dd-subtitle">
                {
                  task.segments.find(
                    (s) =>
                      transport.position >= s.playbackStartSec &&
                      transport.position <
                        s.playbackStartSec +
                          (s.tts?.durationSec || s.endSec - s.startSec),
                  )?.translation
                }
              </div>
            )}
          </div>
          <div className="dd-transport">
            <div className="dd-play-controls">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="上一段"
                disabled={index === 0}
                onClick={() => navigate(-1)}
              >
                <SkipBack />
              </Button>
              <Button
                className="dd-play-button"
                disabled={task.sourceReady===false}
                size="icon"
                aria-label={transport.playing ? "暂停多轨" : "播放多轨"}
                onClick={transport.toggle}
              >
                {transport.playing ? (
                  <Pause fill="currentColor" />
                ) : (
                  <Play fill="currentColor" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="下一段"
                disabled={index >= task.segments.length - 1}
                onClick={() => navigate(1)}
              >
                <SkipForward />
              </Button>
            </div>
            <div className="dd-transport-clock dd-mono">
              {precise(transport.position)}
              <span>/ {clock(total)}</span>
            </div>
            <div className="dd-transport-options">
              <Button
                variant={captions ? "secondary" : "ghost"}
                size="icon-sm"
                aria-label="显示当前译稿字幕"
                aria-pressed={captions}
                title="显示当前译稿字幕"
                onClick={() => setCaptions(!captions)}
              >
                <FileText />
              </Button>
              <Button
                variant={transport.loop ? "secondary" : "ghost"}
                size="icon-sm"
                aria-label="循环当前段"
                aria-pressed={transport.loop}
                title="循环当前段"
                onClick={() => transport.setLoop(!transport.loop)}
              >
                <Repeat2 />
              </Button>
              <Button
                variant={transport.follow ? "secondary" : "ghost"}
                size="sm"
                aria-pressed={transport.follow}
                onClick={() => transport.setFollow(!transport.follow)}
              >
                跟随
              </Button>
            </div>
          </div>
          <input
            className="dd-scrubber dd-range"
            aria-label="全片播放进度"
            type="range"
            min="0"
            max={total}
            step=".01"
            value={transport.position}
            onChange={(e) => transport.seek(Number(e.target.value))}
          />
          <div className="dd-listening">
            <Headphones size={13} />
            <span>监听</span>
            <div>
              {transport.tracks
                .filter((t) => transport.audible(t, transport.tracks))
                .map((t) => (
                  <span className="dd-listening-chip" key={t.key}>
                    {t.speaker ? speakerName(t.speaker) + " · " : ""}
                    {t.label}
                  </span>
                ))}
              {!transport.tracks.some((t) =>
                transport.audible(t, transport.tracks),
              ) && <span className="dd-muted">全部静音</span>}
            </div>
          </div>
          <div className="dd-timeline-heading">
            <h2>
              <AudioLines size={15} />
              多轨时间线
            </h2>
            <div>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="缩小时间线"
                disabled={zoom <= 1}
                onClick={() => zoomTo(Math.max(1, zoom / 1.6))}
              >
                <Minus />
              </Button>
              <span className="dd-mono">{zoom.toFixed(1)}×</span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="放大时间线"
                disabled={zoom >= 100}
                onClick={() => zoomTo(Math.min(100, zoom * 1.6))}
              >
                <Plus />
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  setZoom(1);
                  setViewStart(0);
                }}
              >
                全片
              </Button>
              <Button variant="outline" size="xs" onClick={focusSegment}>
                聚焦段
              </Button>
            </div>
          </div>
          <div className="dd-timeline" ref={timeline}>
            <div
              className="dd-timeline-content"
              style={{ width: `calc(164px + (100% - 164px) * ${zoom})` }}
            >
              <div className="dd-ruler-row">
                <div className="dd-track-label">轨道 / 监听</div>
                <div
                  className="dd-ruler"
                  onClick={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    transport.seek(
                      ((e.clientX - rect.left) / rect.width) * total,
                    );
                  }}
                >
                  {Array.from(
                    { length: Math.min(100, Math.ceil(zoom * 6)) + 1 },
                    (_, i) => (
                      <span
                        key={i}
                        style={{
                          left: `${(i / Math.min(100, Math.ceil(zoom * 6))) * 100}%`,
                        }}
                      >
                        {clock(
                          (i / Math.min(100, Math.ceil(zoom * 6))) * total,
                        )}
                      </span>
                    ),
                  )}
                </div>
              </div>
              {transport.tracks.map((t) => (
                <div
                  className={
                    "dd-track " +
                    (transport.audible(t, transport.tracks) ? "" : "muted")
                  }
                  key={t.key}
                >
                  <div className="dd-track-label">
                    <div className={"dd-track-dot " + t.kind} />
                    <div className="dd-track-name">
                      <strong>
                        {t.speaker ? speakerName(t.speaker) : "背景音"}
                      </strong>
                      <span>{t.label}</span>
                    </div>
                    <div className="dd-track-controls">
                      <button
                        aria-label={`静音 ${t.speaker ? speakerName(t.speaker) + " " : ""}${t.label}`}
                        aria-pressed={t.muted}
                        disabled={!t.available}
                        className={t.muted ? "mute active" : ""}
                        onClick={() => transport.change(t.key, "muted")}
                      >
                        M
                      </button>
                      <button
                        aria-label={`独听 ${t.speaker ? speakerName(t.speaker) + " " : ""}${t.label}`}
                        aria-pressed={t.solo}
                        disabled={!t.available}
                        className={t.solo ? "solo active" : ""}
                        onClick={() => transport.change(t.key, "solo")}
                      >
                        S
                      </button>
                    </div>
                  </div>
                  <div
                    className="dd-track-lane"
                    onDoubleClick={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      transport.seek(
                        ((e.clientX - rect.left) / rect.width) * total,
                      );
                    }}
                  >
                    {t.kind === "background" ? (
                      <div className="dd-background-clip">
                        {t.available ? (
                          <>
                            <Music2 size={12} />
                            {t.label} · {clock(total)}
                          </>
                        ) : (
                          "未提供此轨道"
                        )}
                      </div>
                    ) : (
                      task.segments
                        .filter(
                          (s) =>
                            s.speakerId === t.speaker &&
                            (t.kind === "source" || s.tts),
                        )
                        .map((s) => (
                          <TimelineClip
                            key={s.segmentId}
                            segment={s}
                            kind={t.kind as "source" | "tts"}
                            total={total}
                            zoom={zoom}
                            selected={segment?.segmentId === s.segmentId}
                            adapter={adapter}
                            onSelect={() => choose(s)}
                            onFocus={() => {
                              choose(s);
                              const span = Math.max(8, s.endSec - s.startSec);
                              setZoom(Math.min(100, total / (span * 2)));
                              setViewStart(
                                Math.max(0, s.startSec - span * 0.35),
                              );
                            }}
                          />
                        ))
                    )}
                    <div
                      className="dd-timeline-playhead"
                      style={{ left: `${(transport.position / total) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="dd-timeline-footer">
            <span>
              <kbd>M</kbd> 静音 <kbd>S</kbd> 多轨独听
            </span>
            <span>Ctrl + 滚轮缩放 · Shift + 滚轮横移</span>
          </div>
          <div className="dd-center-bottom">
            <Info size={14} />
            <span>监听设置不会修改最终混音。原声窗与配音落点独立保存。</span>
            <button onClick={() => setPanel("help")}>
              快捷键
              <Keyboard size={13} />
            </button>
          </div>
        </section>
        <aside className="dd-editor" aria-label="当前段编辑">
          {segment && (
            <>
              <div className="dd-editor-heading">
                <div>
                  <span className="dd-eyebrow">当前语义段</span>
                  <h2 className="dd-mono">
                    {segment.segmentId}
                    <QualityBadge segment={segment} />
                  </h2>
                </div>
                <div>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="编辑上一段"
                    disabled={index === 0}
                    onClick={() => navigate(-1)}
                  >
                    <ChevronLeft />
                  </Button>
                  <span>
                    {index + 1}/{task.segments.length}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="编辑下一段"
                    disabled={index >= task.segments.length - 1}
                    onClick={() => navigate(1)}
                  >
                    <ChevronRight />
                  </Button>
                </div>
              </div>
              <div
                className="dd-editor-tabs"
                role="tablist"
                aria-label="段编辑面板"
              >
                {[
                  ["edit", "译稿与配音"],
                  [
                    "versions",
                    `版本 ${(segment.versions?.translations.length || 0) + (segment.versions?.audio.length || 0)}`,
                  ],
                  ["diagnostics", "诊断"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    role="tab"
                    aria-selected={editorTab === key}
                    onClick={() => setEditorTab(key)}
                  >
                    {label}
                    {key === "edit" && dirty && <i className="dd-dirty-dot" />}
                  </button>
                ))}
              </div>
              <div
                className="dd-editor-body"
                key={segment.segmentId}
                ref={editorBody}
              >
                {editorTab === "edit" && (
                  <>
                    <div className="dd-text-label">
                      <span>
                        <span className="dd-language">EN</span>原文
                      </span>
                      <span className="dd-mono">
                        {precise(segment.startSec)} — {precise(segment.endSec)}
                      </span>
                    </div>
                    <p className="dd-source-copy">{segment.sourceText}</p>
                    <div className="dd-reference-caption">
                      <Mic2 size={12} />
                      {speakerName(segment.speakerId)}
                      <span>{segment.emotionReady===false?'情绪参考尚未登记':'本段原声作情绪参考'}</span>
                    </div>
                    <Button
                      variant="outline"
                      className="dd-audition"
                      size="sm"
                      onClick={() =>
                        transport.soloPlay(
                          "source:" + segment.speakerId,
                          segment.startSec,
                        )
                      }
                    >
                      <Play size={12} />
                      试听原声 ·{" "}
                      {(segment.endSec - segment.startSec).toFixed(2)}s
                      <span>同步画面</span>
                    </Button>
                    {task.sourceReady!==false&&<WavePlayer
                      key={segment.segmentId + ":source"}
                      adapter={adapter}
                      segment={segment.segmentId}
                      kind="source"
                      src={adapter.media("emotion", {
                        segment: segment.segmentId,
                      }) || adapter.media('source')}
                      title={segment.emotionReady===false?'原声窗口':'原声与情绪参考'}
                      sync={{
                        position: transport.position,
                        playing: transport.playing,
                        start: segment.startSec,
                        pause: transport.pause,
                        seek: transport.seek,
                        play: () =>
                          transport.soloPlay(
                            "source:" + segment.speakerId,
                            transport.position >= segment.startSec &&
                              transport.position < segment.endSec
                              ? transport.position
                              : segment.startSec,
                          ),
                      }}
                      axis={segment.endSec - segment.startSec}
                      onPlay={transport.pause}
                    />}
                    {adapter.canWrite&&<div className="dd-save-row">
                      <Button variant="outline" size="sm" disabled={!can('prepare-emotion')||busy}
                        onClick={()=>ask('提取原声情绪参考','使用当前段准确原声窗口，保留源时间。只登记参考候选，不判定情绪有效性或听感通过。',
                          ()=>write('prepare-emotion',{segmentId:segment.segmentId}))}>提取情绪参考</Button>
                      <Button variant="outline" size="sm" disabled={!can('prepare-voice')||busy||segment.endSec-segment.startSec<3}
                        onClick={()=>ask('提取本集固定音色参考','从当前角色段开头取最多10秒原声，至少3秒；固定音色与逐段情绪参考分别登记。身份与听感须独立复核。',
                          ()=>write('prepare-voice',{segmentId:segment.segmentId}))}>提取音色参考</Button>
                    </div>}
                    <div className="dd-text-label">
                      <span>
                        <span className="dd-language zh">中</span>中文译稿
                        {dirty && <b className="dd-unsaved">未保存</b>}
                      </span>
                      <span>{draft.length} 字</span>
                    </div>
                    {segment.translationCandidate && !segment.translation && (
                      <div className="dd-note">
                        候选尚未独立复核。
                        <Button
                          size="xs"
                          variant="link"
                          onClick={() =>
                            setDrafts((d) => ({
                              ...d,
                              [segment.segmentId]:
                                segment.translationCandidate!,
                            }))
                          }
                        >
                          带入候选
                        </Button>
                        <p>{segment.translationCandidateFlags?.join("；")}</p>
                      </div>
                    )}
                    {adapter.canWrite&&<div className="dd-save-row">
                      <span>{can('translate')?'同输入复用已有成功候选；不会自动通过审核':'生成需要执行/评分权限及同来源的执行中运行'}</span>
                      <Button size="sm" variant="outline" disabled={!can('translate')||busy||dirty}
                        onClick={()=>ask('生成译稿候选','使用配置的真实模型及相邻原文生成候选。旧译稿保留；生成不等于独立语义审核。',
                          ()=>write('translate',{segmentId:segment.segmentId}))}><Sparkles size={13}/>生成译稿候选</Button>
                    </div>}
                    <Textarea
                      aria-label="中文译稿"
                      readOnly={!can('translation')}
                      className="dd-translation"
                      value={draft}
                      onChange={(e) =>
                        setDrafts((d) => ({
                          ...d,
                          [segment.segmentId]: e.target.value,
                        }))
                      }
                    />
                    <div className="dd-save-row">
                      <span>
                        {dirty
                          ? "保存为新版本，旧译稿保留"
                          : "修改译稿后，配音需要重新生成"}
                      </span>
                      <Button
                        size="sm"
                        disabled={
                          !can('translation') || busy || !dirty || !draft.trim() || job?.state === "running"
                        }
                        onClick={() =>
                          ask(
                            "保存译稿新版本",
                            "新建译稿修订，保留旧译稿及对应配音；新稿需要重新复核和配音。",
                            async () => {
                              await write("translation", {
                                segmentId: segment.segmentId,
                                translation: draft,
                              });
                              setDrafts((d) => {
                                const next = { ...d };
                                delete next[segment.segmentId];
                                return next;
                              });
                            },
                          )
                        }
                      >
                        <Save />
                        保存译稿
                      </Button>
                    </div>
                    <div className="dd-section-divider" />
                    <div className="dd-text-label">
                      <span>
                        <AudioLines size={14} />
                        当前中文配音
                      </span>
                      <span>
                        {segment.tts
                          ? `音频 v${segment.tts.attempt}`
                          : "未生成"}
                      </span>
                    </div>
                    {segment.tts ? (
                      <>
                        <WavePlayer
                          key={segment.segmentId + ":" + segment.tts.attempt}
                          adapter={adapter}
                          segment={segment.segmentId}
                          kind="tts"
                          src={adapter.media("tts", {
                            segment: segment.segmentId,
                            attempt: segment.tts.attempt,
                          })}
                          title={"中文配音 v" + segment.tts.attempt}
                          sync={{
                            position: transport.position,
                            playing: transport.playing,
                            start: segment.playbackStartSec,
                            pause: transport.pause,
                            seek: transport.seek,
                            play: () =>
                              transport.soloPlay(
                                "tts:" + segment.speakerId,
                                transport.position >=
                                  segment.playbackStartSec &&
                                  transport.position <
                                    segment.playbackStartSec +
                                      segment.tts!.durationSec
                                  ? transport.position
                                  : segment.playbackStartSec,
                              ),
                          }}
                          axis={segment.endSec - segment.startSec}
                          offset={segment.playbackStartSec - segment.startSec}
                          onPlay={transport.pause}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          className="dd-audition"
                          onClick={() =>
                            transport.soloPlay(
                              "tts:" + segment.speakerId,
                              segment.playbackStartSec,
                            )
                          }
                        >
                          <Play />
                          配音与画面同步试听
                          <ArrowRight size={12} />
                        </Button>
                      </>
                    ) : (
                      <div className="dd-empty-audio">
                        <AudioLines size={24} />
                        <span>当前段还没有配音</span>
                      </div>
                    )}
                    <div className="dd-position">
                      <label>
                        配音落点 <small>秒</small>
                        <Input
                          aria-label="配音播放起点"
                          disabled={!can('playback')}
                          type="number"
                          min="0"
                          max={total}
                          step=".01"
                          value={placement}
                          onChange={(e) => setPlacement(e.target.value)}
                        />
                      </label>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          !can('playback') || !placement.trim() ||
                          !Number.isFinite(Number(placement)) ||
                          Number(placement) < 0 ||
                          Number(placement) > total ||
                          Number(placement) === segment.playbackStartSec
                        }
                        onClick={() =>
                          ask(
                            "更新配音落点",
                            "只改变配音播放位置；原文窗口与情绪参考不变。",
                            () =>
                              write("playback", {
                                segmentId: segment.segmentId,
                                startSec: Number(placement),
                              }),
                          )
                        }
                      >
                        更新位置
                      </Button>
                    </div>
                    <p className="dd-small-note">
                      原声窗 {precise(segment.startSec)}–
                      {precise(segment.endSec)}，不会随配音落点移动。
                    </p>
                    <div className="dd-generation">
                      <div>
                        <Sparkles size={14} />
                        <strong>重新配音</strong>
                        <span>保留所有尝试</span>
                      </div>
                      <div>
                        <label>
                          时长系数
                          <Input
                            aria-label="TTS 时长系数"
                            disabled={!can('synthesize')}
                            type="number"
                            min=".90"
                            max="1.10"
                            step=".01"
                            value={factor}
                            onChange={(e) => setFactor(e.target.value)}
                          />
                        </label>
                        <Button
                          size="sm"
                          disabled={
                            !can('synthesize') ||
                            dirty ||
                            !segment.translation ||
                            !Number.isFinite(Number(factor)) ||
                            Number(factor) < 0.9 ||
                            Number(factor) > 1.1 ||
                            job?.state === "running"
                          }
                          onClick={() =>
                            ask(
                              "生成当前段的新配音",
                              "将调用原工作台的实际配音服务，产生新音频尝试；不会覆盖旧音频。",
                              async () => {
                                const result = (await write("synthesize", {
                                  segmentId: segment.segmentId,
                                  durationFactor: Number(factor),
                                })) as { jobId: string };
                                setJob({
                                  id: result.jobId,
                                  segment: segment.segmentId,
                                  state: "running",
                                });
                              },
                            )
                          }
                        >
                          <Sparkles />
                          生成当前段
                        </Button>
                      </div>
                      <p>
                        {!can('synthesize')&&adapter.canWrite ? '当前入口尚未开放配音生成。' : '允许 0.90–1.10；候选须复核内容、时序与听感。'}
                        {dirty ? "请先保存译稿。" : ""}
                      </p>
                    </div>
                  </>
                )}
                {editorTab === "versions" && (
                  <>
                    <div className="dd-note">
                      <History size={14} />
                      选择声音版本会同步恢复它绑定的译稿。各版波形使用同一个原声时间尺。
                    </div>
                    <h3 className="dd-version-title">
                      <FileText size={14} />
                      译稿版本
                      <span>{segment.versions?.translations.length || 0}</span>
                    </h3>
                    {segment.versions?.translations.map((v) => (
                      <details className="dd-version" key={v.revision}>
                        <summary>
                          <span className="dd-version-icon">文</span>
                          <div>
                            <strong>译稿 v{v.revision}</strong>
                            <small>
                              {v.sources?.map((s) => s.model).join(" / ") ||
                                "来源未记录"}
                            </small>
                          </div>
                          {v.selected ? (
                            <Badge variant="secondary">当前</Badge>
                          ) : (
                            <ChevronDown size={14} />
                          )}
                        </summary>
                        <p>{v.translation}</p>
                        <p className="dd-small-note">
                          整集工程代理：
                          {v.sources?.[0]?.overallScore ?? "未评分"}
                          ，不代表语义准确率。
                        </p>
                        {!v.selected && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!can('select-version')}
                            onClick={() =>
                              ask(
                                "切换当前译稿",
                                "保留历史版本与编辑草稿，按所选译稿重新建立当前版本。",
                                () =>
                                  write("select-version", {
                                    segmentId: segment.segmentId,
                                    revision: v.revision,
                                  }),
                              )
                            }
                          >
                            设为当前译稿
                          </Button>
                        )}
                      </details>
                    ))}
                    <h3 className="dd-version-title">
                      <AudioLines size={14} />
                      声音版本<span>{segment.versions?.audio.length || 0}</span>
                    </h3>
                    {segment.versions?.audio.map((v) => (
                      <details
                        className="dd-version"
                        key={v.attempt}
                        open={v.selected || undefined}
                      >
                        <summary>
                          <span className="dd-version-icon audio">声</span>
                          <div>
                            <strong>音频 v{v.attempt}</strong>
                            <small>
                              {v.provider || "模型未登记"} · 译稿 v
                              {v.translationRevision || "?"}
                            </small>
                          </div>
                          {v.selected ? (
                            <Badge variant="secondary">当前</Badge>
                          ) : (
                            <ChevronDown size={14} />
                          )}
                        </summary>
                        <p>{v.translation || "未匹配历史译稿"}</p>
                        <p className="dd-small-note">
                          内容：{v.review || "按历史记录"} · 情绪：
                          {v.emotionMode === "segment-source"
                            ? "本段原声"
                            : v.emotionMode || "未记录"}
                        </p>
                        <p className="dd-small-note">
                          {v.findings?.join("；")}
                        </p>
                        <WavePlayer
                          key={segment.segmentId + ":history:" + v.attempt}
                          adapter={adapter}
                          segment={segment.segmentId}
                          kind={"tts-" + v.attempt}
                          src={adapter.media("tts", {
                            segment: segment.segmentId,
                            attempt: v.attempt,
                          })}
                          title={"历史配音 v" + v.attempt}
                          axis={segment.endSec - segment.startSec}
                          onPlay={transport.pause}
                        />
                        <ScoreCard score={v.score} />
                        {!v.selected && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!can('select-version')||!v.translation}
                            onClick={() =>
                              ask(
                                "切换当前声音版本",
                                "恢复它绑定的译稿，并重新匹配成片缓存。当前编辑草稿会继续保留。",
                                () =>
                                  write("select-version", {
                                    segmentId: segment.segmentId,
                                    attempt: v.attempt,
                                  }),
                              )
                            }
                          >
                            设为当前声音版本
                          </Button>
                        )}
                      </details>
                    ))}
                  </>
                )}
                {editorTab === "diagnostics" && (
                  <>
                    <div className="dd-note">
                      <Info size={15} />
                      工程代理不能代替语义、声音身份或自然度的独立审核。
                    </div>
                    <ScoreCard
                      score={
                        segment.versions?.audio.find((v) => v.selected)?.score
                      }
                    />
                    <div className="dd-text-label">
                      <span>当前段对齐</span>
                      <QualityBadge segment={segment} />
                    </div>
                    <div className="dd-diagnostic-grid">
                      <div>
                        原声窗口
                        <b>{(segment.endSec - segment.startSec).toFixed(2)}s</b>
                      </div>
                      <div>
                        配音长度
                        <b>
                          {segment.tts?.durationSec.toFixed(2) ?? "未生成"}s
                        </b>
                      </div>
                      <div>
                        活动起点差
                        <b>
                          {signed(
                            segment.quality?.metrics?.alignment?.onsetDeltaSec,
                          )}
                        </b>
                      </div>
                      <div>
                        活动终点差
                        <b>
                          {signed(
                            segment.quality?.metrics?.alignment?.offsetDeltaSec,
                          )}
                        </b>
                      </div>
                    </div>
                    {segment.quality?.issues?.map((s, i) => (
                      <p className="dd-issue bad" key={i}>
                        {s}
                      </p>
                    ))}
                    {segment.quality?.advisories?.map((s, i) => (
                      <p className="dd-issue" key={i}>
                        {s}
                      </p>
                    ))}
                    {segment.quality?.contextualAcceptance && (
                      <details className="dd-evidence" open>
                        <summary>情境取舍与绑定证据</summary>
                        <p>{segment.quality.contextualAcceptance.reason}</p>
                        <p>{segment.quality.contextualAcceptance.evidence}</p>
                      </details>
                    )}
                    {segment.quality?.metrics?.alignment?.repairPlan?.map(
                      (s, i) => (
                        <p className="dd-small-note" key={i}>
                          · {s}
                        </p>
                      ),
                    )}
                    <Button
                      variant="outline"
                      className="dd-full"
                      disabled={!can('validate')}
                      onClick={() =>
                        ask(
                          "运行全片结构验收",
                          "会对原任务重新进行结构验收并保存报告；不自动确认语义、音色身份或自然度。",
                          () => write("validate", {}),
                        )
                      }
                    >
                      <ShieldCheck />
                      运行全片结构验收
                    </Button>
                  </>
                )}
              </div>
              <footer className="dd-editor-footer">
                <ShieldCheck size={13} />
                <span>{!can('translation') ? '只读权限 · 可检查版本与试听' : '版本可追溯 · 人耳维度未测时保持未测'}</span>
              </footer>
            </>
          )}
        </aside>
      </main>
      <footer className="dd-statusbar">
        <span>
          <i className="dd-live-dot" />
          本机工作台 · {taskId}
        </span>
        <span>
          {job ? (
            job.state === "running" ? (
              <>
                <LoaderCircle size={12} className="animate-spin" />
                {job.segment} 配音生成中
              </>
            ) : job.state === "done" ? (
              <>
                <Check size={12} />
                {job.segment} 新配音已生成
              </>
            ) : (
              <>
                <Info size={12} />
                {job.error || "配音状态未知"}
                <button onClick={() => setJob({ ...job, state: "running" })}>
                  重查
                </button>
              </>
            )
          ) : (
            <>
              <CheckCircle2 size={12} />
              真实任务已载入
            </>
          )}
        </span>
        <span>
          空格 播放 / 暂停 <kbd>J</kbd>
          <kbd>K</kbd> 切段
        </span>
      </footer>
      <Dialog
        open={!!confirm}
        onOpenChange={(open) => {
          if (!open && !busy) setConfirm(null);
        }}
      >
        <DialogContent className="dd-floating">
          <DialogHeader>
            <DialogTitle>{confirm?.title}</DialogTitle>
            <DialogDescription>{confirm?.description}</DialogDescription>
          </DialogHeader>
          <p className="dd-small-note">
            目标：{taskId} / {segment?.segmentId}
            。此操作使用当前业务服务。
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setConfirm(null)}
            >
              取消
            </Button>
            <Button disabled={busy} onClick={apply}>
              {busy && <LoaderCircle className="animate-spin" />}确认执行
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Sheet
        open={!!panel}
        onOpenChange={(open) => {
          if (!open) setPanel(null);
        }}
      >
        <SheetContent className="dd-floating dd-sheet" showCloseButton={false}>
          <SheetClose className="dd-sheet-close" aria-label="关闭面板">
            <X size={16} />
          </SheetClose>
          <SheetHeader>
            <SheetTitle>
              {panel === "voices"
                ? "说话人与固定音色"
                : panel === "quality"
                  ? "质量与交付"
                  : panel === "strategies"
                    ? "成片与策略对比"
                    : "工作区快捷键"}
            </SheetTitle>
            <SheetDescription>
              {panel === "voices"
                ? "每集固定音色，情绪参考独立取本段原声。"
                : panel === "quality"
                  ? "历史证据和当前档位分开显示；未测不视为通过。"
                  : panel === "strategies"
                    ? "同源版本的真实试听与工程代理。"
                    : "减少鼠标往返，保持画面、时间线与当前段上下文。"}
            </SheetDescription>
          </SheetHeader>
          <div className="dd-sheet-body">
            {panel === "help" && (
              <>
                <div className="dd-shortcuts">
                  {[
                    ["空格", "播放 / 暂停多轨"],
                    ["← / →", "后退 / 前进 5 秒"],
                    ["J / K", "下一段 / 上一段"],
                    ["Ctrl + 滚轮", "缩放时间线"],
                    ["Shift + 滚轮", "横向移动时间线"],
                    ["M / S 按钮", "静音 / 多轨独听"],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <span>{v}</span>
                      <kbd>{k}</kbd>
                    </div>
                  ))}
                </div>
                <p className="dd-small-note">
                  文本输入时不会触发播放快捷键；切段保留未保存草稿。关闭页面前提示未保存内容。
                </p>
                <a
                  href={adapter.canWrite?backHref:`http://127.0.0.1:8765/task/${taskId}`}
                  target="_blank"
                  rel="noreferrer"
                  className="dd-link"
                >
                  {adapter.canWrite?'返回项目':'打开原工作台对照'}
                  <ExternalLink size={13} />
                </a>
              </>
            )}
            {panel === "quality" && (
              <>
                <div className="dd-delivery-state">
                  <ShieldCheck size={28} />
                  <div>
                    <strong>{task.status}</strong>
                    <p>
                      {task.latestValidation?.decision === "passed"
                        ? "历史结构报告通过"
                        : "当前结构验收未通过或未登记"}{" "}
                      · 听感与身份需独立审核
                    </p>
                  </div>
                </div>
                <div className="dd-diagnostic-grid">
                  <div>
                    素材时长<b>{clock(total)}</b>
                  </div>
                  <div>
                    已配音
                    <b>
                      {task.segments.filter((s) => s.tts).length}/
                      {task.segments.length}
                    </b>
                  </div>
                  <div>
                    结构待返修<b>{issues}</b>
                  </div>
                  <div>
                    历史完整 RTF
                    <b>
                      {task.production?.measured
                        ? task.production.rtfUnrounded?.toFixed(3) || "未计量"
                        : "未计量"}
                    </b>
                  </div>
                </div>
                <p className="dd-small-note">
                  RTF 目标 ≤ 0.5。历史报告不代表本次重新生产，也不代表速度达标。
                </p>
                <h3 className="dd-version-title">交付质量</h3>
                <select
                  className="dd-select"
                  aria-label="交付质量"
                  value={quality || task.qualityProfile?.id || ""}
                  onChange={(e) => setQuality(e.target.value)}
                >
                  {Object.entries(task.qualityCatalog?.profiles || {}).map(
                    ([id, p]) => (
                      <option key={id} value={id}>
                        {p.label}
                      </option>
                    ),
                  )}
                </select>
                <p className="dd-small-note">
                  {
                    task.qualityCatalog?.profiles[
                      quality || task.qualityProfile?.id || ""
                    ]?.description
                  }
                </p>
                <Button
                  size="sm"
                  disabled={!can('quality-profile')||!quality || quality === task.qualityProfile?.id}
                  onClick={() =>
                    ask(
                      "更新交付质量档位",
                      "保留所有历史与返工记录。已有验收不能自动应用到新档位，需重新验收。",
                      () => write("quality-profile", { quality }),
                    )
                  }
                >
                  应用所选档位
                </Button>
                <div className="dd-section-divider" />
                <p className="dd-small-note">
                  共同要求：{task.qualityCatalog?.commonRequirements.join("；")}
                </p>
                <details className="dd-evidence">
                  <summary>历史验收出处与计量明细</summary>
                  <p>
                    最近验收：{task.latestValidation?.at || "未登记"} · 档位{" "}
                    {task.latestValidation?.qualityProfile?.label || "未登记"}
                  </p>
                  <pre>{JSON.stringify(task.production, null, 2)}</pre>
                </details>
                <Button
                  className="dd-full"
                  disabled={!can('validate')}
                  variant="outline"
                  onClick={() =>
                    ask(
                      "运行全片结构验收",
                      "保留历史报告，实际检查当前选版。语义与人耳维度不能由此自动判定通过。",
                      () => write("validate", {}),
                    )
                  }
                >
                  <ShieldCheck />
                  运行结构验收
                </Button>
              </>
            )}
            {panel === "strategies" && (
              <>
                <h3 className="dd-version-title">
                  当前组合成片{" "}
                  <Badge variant="outline">
                    {task.renderCacheCount || 0} 个缓存
                  </Badge>
                </h3>
                {task.render ? (
                  <video
                    className="dd-render-video"
                    controls
                    preload="metadata"
                    src={adapter.media("render")}
                    onPlay={(event) => {
                      transport.pause();
                      stopAuditions(event.currentTarget);
                    }}
                  />
                ) : (
                  <div className="dd-empty">尚未生成当前组合的成片</div>
                )}
                <p className="dd-small-note">
                  {task.strategyComparison?.method ||
                    "未登记额外策略对照。现有成片与各版分段仍可独立试听。"}
                </p>
                {task.strategyComparison?.strategies.map((s) => (
                  <div className="dd-strategy" key={s.id}>
                    <h3>
                      {s.label}
                      <span>{s.overallScore ?? "未评分"}</span>
                    </h3>
                    <div className="dd-metrics">
                      <div>
                        内容代理<b>{s.contentScore ?? "未测"}</b>
                      </div>
                      <div>
                        时序代理<b>{s.timingScore ?? "未测"}</b>
                      </div>
                      <div>
                        覆盖率
                        <b>
                          {s.coverage == null
                            ? "未测"
                            : `${(s.coverage * 100).toFixed(1)}%`}
                        </b>
                      </div>
                    </div>
                    <p className="dd-small-note">
                      音频 {s.audioCount}/{s.segments} · 时序通过{" "}
                      {s.timingPassed}/{s.segments} · 听感{" "}
                      {s.listening || "未测"}
                    </p>
                    {s.audio && (
                      <WavePlayer
                        adapter={adapter}
                        title={s.label + "整集试听"}
                        data={s.waveform}
                        src={adapter.media("strategy", { strategy: s.id })}
                        axis={total}
                        onPlay={transport.pause}
                      />
                    )}
                  </div>
                ))}
              </>
            )}
            {panel === "voices" &&
              [...new Set(task.segments.map((s) => s.speakerId))].map((id) => (
                <VoicePanel
                  key={id}
                  id={id}
                  voice={task.voices?.[id] || { revision: 0 }}
                  name={speakerName(id)}
                  adapter={adapter}
                  pause={transport.pause}
                  write={write}
                  ask={ask}
                />
              ))}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function VoicePanel({
  id,
  voice,
  name,
  adapter,
  pause,
  write,
  ask,
}: {
  id: string;
  voice: NonNullable<Task["voices"]>[string];
  name: string;
  adapter: TaskAdapter;
  pause: () => void;
  write: (action: string, body: unknown) => Promise<unknown>;
  ask: (
    title: string,
    description: string,
    action: () => Promise<unknown>,
  ) => void;
}) {
  const can=(action:string)=>adapter.canWrite?.(action)??true;
  const [display, setDisplay] = useState(name),
    [candidates, setCandidates] = useState<
      {
        segmentId: string;
        startSec: number;
        quality: { activeSec: number; noiseFloorDbfsProxy: number };
        sourceUnitsPerActiveSec: number;
      }[]
    >([]),
    [loading, setLoading] = useState(false);
  return (
    <section className="dd-voice-panel">
      <div className="dd-text-label">
        <span>
          <Mic2 size={14} />
          {name || id}
        </span>
        <Badge variant="outline">
          {voice.revision ? `参考 v${voice.revision}` : "尚未登记参考"}
        </Badge>
      </div>
      <div className="dd-voice-name">
        <Input
          aria-label={(name || id) + "说话人显示名"}
          value={display}
          maxLength={40}
          onChange={(e) => setDisplay(e.target.value)}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={!can('speaker-name')||!display.trim() || display === name}
          onClick={() =>
            ask("更新说话人显示名", "稳定说话人 ID 不变，只更新显示名。", () =>
              write("speaker-name", { speakerId: id, name: display.trim() }),
            )
          }
        >
          保存名称
        </Button>
      </div>
      <p className="dd-small-note">
        {voice.voiceMode || "模式未登记"} ·{" "}
        {voice.selectionScope === "episode" ? "本集独立提取" : "旧版 / 手动"} ·
        身份听审 {voice.identityReview || "未测"}
      </p>
      {(voice.sourceVoiceWaveform || adapter.canWrite) && (
        <WavePlayer
          adapter={adapter}
          title="固定音色 · 原声"
          segment={adapter.canWrite?id:undefined}
          kind={adapter.canWrite?'voice-source':undefined}
          key={id + ":source:" + voice.revision}
          data={voice.sourceVoiceWaveform}
          src={adapter.media("voice", { speaker: id, kind: "sourceVoice" })}
          onPlay={pause}
        />
      )}
      {!!voice.revision && (
        <WavePlayer
          adapter={adapter}
          title="固定音色 · 处理后"
          segment={adapter.canWrite?id:undefined}
          kind={adapter.canWrite?'voice-processed':undefined}
          key={id + ":prepared:" + voice.revision}
          data={voice.voiceWaveform}
          src={adapter.media("voice", { speaker: id, kind: "voice" })}
          onPlay={pause}
        />
      )}
      <details className="dd-evidence">
        <summary>处理方案与前后技术指标</summary>
        {Object.keys(voice.sourceVoiceProfile || {}).length > 0 ? (
          <Table aria-label="音色参考处理前后技术指标">
            <TableHeader><TableRow><TableHead>指标</TableHead><TableHead>原声</TableHead><TableHead>处理后</TableHead></TableRow></TableHeader>
            <TableBody>{Object.entries(voice.sourceVoiceProfile || {}).map(([key, value]) => (
              <TableRow key={key}><TableCell>{key}</TableCell>
                <TableCell className="dd-mono">{typeof value === 'number' ? value.toFixed(key.includes('时长') ? 3 : 5) : String(value)}</TableCell>
                <TableCell className="dd-mono">{voice.voiceProfile?.[key] == null ? '未测' : voice.voiceProfile[key].toFixed(key.includes('时长') ? 3 : 5)}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        ) : <p>尚未登记处理前后的技术指标。</p>}
        <p>这些技术指标不等于身份或自然度通过。</p>
        {voice.processing && <details className="dd-evidence"><summary>查看完整处理证据</summary><pre>{JSON.stringify(voice.processing, null, 2)}</pre></details>}
      </details>
      <Button
        variant="outline"
        className="dd-full"
        disabled={!can('voice-candidates')||loading}
        onClick={async () => {
          setLoading(true);
          try {
            const r = (await write("voice-candidates", { speakerId: id })) as {
              candidates: typeof candidates;
            };
            setCandidates(r.candidates);
          } catch (e) {
            toast.error((e as Error).message);
          } finally {
            setLoading(false);
          }
        }}
      >
        {loading ? <LoaderCircle className="animate-spin" /> : <Search />}
        重新分析本集音色候选
      </Button>
      {candidates.map((c, i) => (
        <div className="dd-candidate" key={c.segmentId}>
          <strong>
            候选 {i + 1} · {c.segmentId}
          </strong>
          <p className="dd-small-note">
            有效发声 {c.quality.activeSec}s · 噪声代理{" "}
            {c.quality.noiseFloorDbfsProxy} dBFS · 语速{" "}
            {c.sourceUnitsPerActiveSec}
          </p>
          {["prepared", "raw"].map((kind) => (
            <div key={kind}>
              <WavePlayer
                adapter={adapter}
                title={kind === "raw" ? "候选原声" : "候选净化版"}
                src={adapter.media("candidate", {
                  speaker: id,
                  number: i + 1,
                  kind,
                })}
                onPlay={pause}
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  ask(
                    "登记固定音色",
                    "更新本集固定音色参考；身份与自然度仍须试听确认。情绪仍自动取每段原声。",
                    () =>
                      write("register-voice", {
                        speakerId: id,
                        voice: { number: i + 1, kind },
                      }),
                  )
                }
              >
                将{kind === "raw" ? "原声" : "净化版"}设为音色
              </Button>
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}
