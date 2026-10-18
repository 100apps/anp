import { memo, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, Pause, Volume2 } from "lucide-react";
import { Button } from "@console/components/ui/button";
import type { TaskAdapter, Wave } from "./model";
import { clock } from "./model";
import { stopAuditions } from "./audition";

function WaveformView({
  data,
  axis,
  offset = 0,
}: {
  data?: Wave;
  axis?: number;
  offset?: number;
}) {
  if (!data?.peaks?.length)
    return <div className="dd-wave-empty">波形未载入</div>;
  const duration = axis || data.durationSec,
    step =
      ((data.durationSec / Math.max(0.01, duration)) * 1000) /
      data.peaks.length,
    start = (offset / Math.max(0.01, duration)) * 1000;
  return (
    <svg
      className="dd-wave"
      viewBox="0 0 1000 60"
      preserveAspectRatio="none"
      aria-label="实际音频波形"
    >
      <line
        x1="0"
        y1="30"
        x2="1000"
        y2="30"
        stroke="currentColor"
        opacity=".15"
      />
      {data.peaks.map((p, i) => (
        <rect
          key={i}
          x={start + i * step}
          y={30 - Math.max(1, Math.min(28, p * 52))}
          width={Math.max(0.6, step * 0.7)}
          height={Math.max(2, Math.min(56, p * 104))}
          fill="currentColor"
        />
      ))}
    </svg>
  );
}
export const Waveform = memo(WaveformView);
export function WavePlayer({
  adapter,
  segment,
  kind,
  src,
  title,
  data,
  axis,
  offset = 0,
  onPlay,
  sync,
}: {
  adapter: TaskAdapter;
  segment?: string;
  kind?: string;
  src: string;
  title: string;
  data?: Wave;
  axis?: number;
  offset?: number;
  onPlay: () => void;
  sync?: {
    position: number;
    playing: boolean;
    start: number;
    play: () => void;
    pause: () => void;
    seek: (point: number) => void;
  };
}) {
  const audio = useRef<HTMLAudioElement>(null),
    root = useRef<HTMLDivElement>(null),
    [visible, setVisible] = useState(false),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [playing, setPlaying] = useState(false),
    [error, setError] = useState(false),
    [reconnecting,setReconnecting]=useState(false),
    [reconnectError,setReconnectError]=useState('');
  useEffect(() => {
    const media = audio.current;
    return () => media?.pause();
  }, []);
  useEffect(() => {
    if (!root.current) return;
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      {
        root: root.current.closest(".dd-editor-body,.dd-sheet-body"),
        rootMargin: "80px",
      },
    );
    observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const wave = useQuery({
    queryKey: ["dd-review-wave",adapter.cacheKey, src, segment, kind],
    queryFn: () => adapter.wave(segment!, kind!),
    enabled: visible && !!segment && !!kind,
    staleTime: 60000,
  });
  const end =
    axis || duration || data?.durationSec || wave.data?.durationSec || 1;
  const playbackDuration=sync ? axis || wave.data?.durationSec || duration : duration;
  const mediaDuration=(sync&&kind==='source'?axis:0)||data?.durationSec||wave.data?.durationSec||duration||0;
  const now = sync
    ? Math.max(0, Math.min(playbackDuration, sync.position - sync.start))
    : time;
  const running = sync
    ? sync.playing &&
      sync.position >= sync.start &&
      sync.position < sync.start + playbackDuration
    : playing;
  function seek(local: number) {
    if (sync) sync.seek(sync.start + local);
    else if (audio.current) audio.current.currentTime = local;
  }
  return (
    <div
      ref={root}
      className={"dd-wave-player " + (kind?.startsWith("tts") ? "dub" : "")}
    >
      <div className="dd-wave-head">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={(running ? "暂停" : "播放") + title}
          onClick={() => {
            if (sync) {
              running ? sync.pause() : sync.play();
              return;
            }
            const a = audio.current;
            if (!a) return;
            if (!a.paused) a.pause();
            else {
              onPlay();
              stopAuditions(a);
              a.play().catch(() => setError(true));
            }
          }}
        >
          {running ? <Pause /> : <Play />}
        </Button>
        <span>{title}</span>
        <small className="dd-mono">
          {clock(sync?Math.min(now,mediaDuration):now)} / {clock(mediaDuration)}
        </small>
      </div>
      <button
        className="dd-wave-stage"
        aria-label={title + "波形定位"}
        onClick={(e) => {
          if (audio.current) {
            const rect = e.currentTarget.getBoundingClientRect();
            seek(
              Math.max(
                0,
                Math.min(
                  playbackDuration,
                  ((e.clientX - rect.left) / rect.width) * end - offset,
                ),
              ),
            );
          }
        }}
      >
        <Waveform data={data || wave.data} axis={end} offset={offset} />
        <i
          style={{ left: `${Math.min(100, ((now + offset) / end) * 100)}%` }}
        />
      </button>
      <input
        aria-label={title + "播放进度"}
        className="dd-range"
        type="range"
        min="0"
        max={playbackDuration || 1}
        step="0.01"
        value={now}
        onChange={(e) => {
          seek(Number(e.target.value));
        }}
      />
      <div className="dd-wave-axis">
        <span>00:00</span>
        <span>
          {error
            ? "音频无法载入，请检查连接"
            : wave.isError
              ? "波形暂不可用，仍可试听"
              : axis
                ? "与本段原声统一时间尺"
                : "独立试听"}
        </span>
        <span>{clock(end)}</span>
      </div>
      <audio
        ref={audio}
        preload={visible ? "metadata" : "none"}
        src={src}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onPlay={() => {
          setPlaying(true);
          setError(false);
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => setError(true)}
      />
      {(error||wave.isError) && (
        <p role="alert" className="dd-error">
          <Volume2 size={12} />
          {reconnectError||(error?'音频加载失败':'波形暂不可用')}
          <Button variant="ghost" size="sm" disabled={reconnecting} onClick={async()=>{
            setReconnecting(true);setReconnectError('');
            try{
              await adapter.reconnectMedia?.();
              if(error){setError(false);audio.current?.load()}
              if(segment&&kind)await wave.refetch();
            }catch(e){setReconnectError(e instanceof Error?e.message:'媒体连接失败')}
            finally{setReconnecting(false)}
          }}>{reconnecting?'连接中…':'重新连接'}</Button>
        </p>
      )}
    </div>
  );
}
