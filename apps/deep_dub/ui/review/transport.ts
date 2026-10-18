import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { stopAuditions } from "./audition";
import type { Segment, Task, TaskAdapter } from "./model";

export type Track = {
  key: string;
  label: string;
  kind: "source" | "tts" | "background";
  speaker?: string;
  muted: boolean;
  solo: boolean;
  available: boolean;
};
export function useTransport(
  task: Task | undefined,
  adapter: TaskAdapter,
  selected: Segment | undefined,
) {
  const video = useRef<HTMLVideoElement>(null),
    nodes = useRef(new Map<string, HTMLAudioElement>());
  const [position, setPosition] = useState(0),
    [playing, setPlaying] = useState(false),
    [loop, setLoop] = useState(false),
    [follow, setFollow] = useState(false);
  const [states, setStates] = useState<
    Record<string, { muted: boolean; solo: boolean }>
  >({});
  const errors = useRef(new Set<string>()),
    time = useRef(0);
  const tracks: Track[] = task
    ? [...new Set(task.segments.map((s) => s.speakerId))]
        .flatMap<Track>((speaker) =>
          (["source", "tts"] as const).map((kind) => ({
            key: `${kind}:${speaker}`,
            label: kind === "source" ? "原声" : "中文配音",
            kind,
            speaker,
            available:
              kind === "source" ||
              task.segments.some((s) => s.speakerId === speaker && s.tts),
            muted: states[`${kind}:${speaker}`]?.muted ?? kind === "source",
            solo: states[`${kind}:${speaker}`]?.solo ?? false,
          })),
        )
        .concat(
          task.background
            ? (["raw", "processed"] as const).map((kind) => ({
                key: `background:${kind}`,
                label: kind === "raw" ? "原始背景" : "处理后背景",
                kind: "background" as const,
                available: kind !== "raw" || !!task.background?.rawAvailable,
                muted: states[`background:${kind}`]?.muted ?? kind === "raw",
                solo: states[`background:${kind}`]?.solo ?? false,
              }))
            : [],
        )
    : [];
  const current = useRef({ task, tracks, selected, loop });
  current.current = { task, tracks, selected, loop };
  const pause = useCallback(() => {
    video.current?.pause();
    nodes.current.forEach((n) => n.pause());
    setPlaying(false);
  }, []);
  const audible = (track: Track, all: Track[]) =>
    track.available &&
    !track.muted &&
    (!all.some((t) => t.solo && t.available) || track.solo);
  const sync = useCallback(
    (point: number, run: boolean) => {
      const { task, tracks } = current.current;
      if (!task) return;
      const active = new Set<string>();
      const node = (
        key: string,
        src: string,
        local: number,
        on: boolean,
        gain = 1,
      ) => {
        if (!on) {
          nodes.current.get(key)?.pause();
          return;
        }
        active.add(key);
        let audio = nodes.current.get(key);
        if (!audio) {
          audio = new Audio(src);
          audio.preload = "metadata";
          audio.volume = Math.max(0, Math.min(1, gain));
          nodes.current.set(key, audio);
          audio.addEventListener("loadedmetadata", () => {
            audio!.currentTime = Number(audio!.dataset.wanted) || 0;
          });
        }
        audio.dataset.wanted = String(local);
        if (audio.readyState >= 1 && Math.abs(audio.currentTime - local) > 0.18)
          audio.currentTime = Math.max(
            0,
            Math.min(local, Math.max(0, audio.duration - 0.01)),
          );
        if (run && audio.paused)
          audio.play().catch(() => {
            if (!errors.current.has(key)) {
              errors.current.add(key);
              toast.error("部分监听音轨无法播放，请检查媒体连接");
            }
          });
        else if (!run) audio.pause();
      };
      const sourceOn = tracks.some(
        (t) =>
          t.kind === "source" &&
          audible(t, tracks) &&
          task.segments.some(
            (s) =>
              s.speakerId === t.speaker &&
              point >= s.startSec &&
              point < s.endSec,
          ),
      );
      node("source", adapter.media("source"), point, sourceOn);
      for (const track of tracks) {
        if (track.kind === "background")
          node(
            track.key,
            adapter.media("background", { kind: track.key.split(":")[1] }),
            point,
            audible(track, tracks),
            task.background?.gain ?? 1,
          );
      }
      for (const seg of task.segments) {
        if (!seg.tts) continue;
        const track = tracks.find((t) => t.key === `tts:${seg.speakerId}`),
          local = point - seg.playbackStartSec;
        if (
          track &&
          audible(track, tracks) &&
          local >= 0 &&
          local < seg.tts.durationSec
        )
          node(
            `${seg.segmentId}:${seg.tts.attempt}`,
            adapter.media("tts", {
              segment: seg.segmentId,
              attempt: seg.tts.attempt,
            }),
            local,
            true,
          );
      }
      nodes.current.forEach((n, k) => {
        if (!active.has(k)) n.pause();
      });
    },
    [adapter],
  );
  const seek = useCallback(
    (point: number) => {
      const duration = current.current.task?.source.durationSec || 0;
      point = Math.max(0, Math.min(duration, point));
      time.current = point;
      setPosition(point);
      if (video.current) {
        video.current.dataset.wanted = String(point);
        if (video.current.readyState >= 1) video.current.currentTime = point;
      }
      sync(point, !!video.current && !video.current.paused);
    },
    [sync],
  );
  const play = useCallback(() => {
    const v = video.current;
    if (!v) return;
    stopAuditions();
    if (v.ended) seek(0);
    v.muted = true;
    v.play().catch(() => toast.error("同步画面未就绪，请检查素材或重新加载"));
    sync(time.current, true);
  }, [seek, sync]);
  const toggle = useCallback(() => {
    video.current?.paused ? play() : pause();
  }, [play, pause]);
  useEffect(() => {
    let frame = 0;
    let paintedAt = 0;
    const tick = () => {
      const v = video.current;
      if (!v) return;
      let point = v.currentTime;
      const c = current.current;
      if (c.loop && c.selected) {
        const end = Math.max(
          c.selected.endSec,
          c.selected.playbackStartSec + (c.selected.tts?.durationSec || 0),
        );
        if (point >= end) {
          point = c.selected.startSec;
          v.currentTime = point;
        }
      }
      time.current = point;
      // 声音每帧同步；文本/表单不需要随显示器刷新率重绘。
      const stamp = performance.now();
      if (stamp - paintedAt >= 50) {
        setPosition(point);
        paintedAt = stamp;
      }
      sync(point, !v.paused);
      frame = requestAnimationFrame(tick);
    };
    if (playing) frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, sync]);
  useEffect(() => {
    sync(time.current, playing);
  }, [states, playing, sync]);
  useEffect(() => {
    const owned = nodes.current;
    return () => {
      video.current?.pause();
      owned.forEach((n) => {
        n.pause();
        n.removeAttribute("src");
        n.load();
      });
      owned.clear();
    };
  }, [adapter]);
  const change = (key: string, field: "muted" | "solo") =>
    setStates((old) => ({
      ...old,
      [key]: {
        muted:
          field === "solo" && !tracks.find((t) => t.key === key)?.solo
            ? false
            : (tracks.find((t) => t.key === key)?.muted ?? false),
        solo: tracks.find((t) => t.key === key)?.solo ?? false,
        ...{ [field]: !tracks.find((t) => t.key === key)?.[field] },
      },
    }));
  const soloPlay = (key: string, point: number) => {
    setStates(
      Object.fromEntries(
        tracks.map((t) => [
          t.key,
          { muted: t.key === key ? false : t.muted, solo: t.key === key },
        ]),
      ),
    );
    seek(point);
    play();
  };
  return {
    video,
    position,
    playing,
    setPlaying,
    loop,
    setLoop,
    follow,
    setFollow,
    tracks,
    audible,
    pause,
    play,
    toggle,
    seek,
    change,
    soloPlay,
  };
}
