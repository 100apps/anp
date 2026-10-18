import { memo, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { precise, type Segment, type TaskAdapter } from "./model";
import { Waveform } from "./wave-player";

function TimelineClipView({
  segment,
  kind,
  total,
  zoom,
  selected,
  adapter,
  onSelect,
  onFocus,
}: {
  segment: Segment;
  kind: "source" | "tts";
  total: number;
  zoom: number;
  selected: boolean;
  adapter: TaskAdapter;
  onSelect: () => void;
  onFocus: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null),
    [visible, setVisible] = useState(false);
  useEffect(() => {
    if (zoom <= 6) {
      setVisible(false);
      return;
    }
    if (!ref.current) return;
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      { root: ref.current.closest(".dd-timeline"), rootMargin: "100px" },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [zoom]);
  const wave = useQuery({
    queryKey: [
      "dd-timeline-wave",
      adapter.cacheKey,
      adapter.media("preview"),
      segment.segmentId,
      kind,
      segment.tts?.attempt,
    ],
    queryFn: () => adapter.wave(segment.segmentId, kind),
    enabled: visible && zoom > 6,
    staleTime: 60000,
  });
  const start = kind === "tts" ? segment.playbackStartSec : segment.startSec,
    length =
      kind === "tts"
        ? segment.tts!.durationSec
        : segment.endSec - segment.startSec;
  return (
    <button
      ref={ref}
      aria-label={`${kind === "tts" ? "中文配音" : "原声"} ${segment.segmentId}`}
      title={`${segment.segmentId} · ${precise(start)} · ${kind === "source" ? segment.sourceText : segment.translation}`}
      className={
        "dd-clip " +
        kind +
        " " +
        (selected ? "selected " : "") +
        (segment.quality?.decision === "failed" ? "failed" : "")
      }
      style={{
        left: `${(start / total) * 100}%`,
        width: `${(length / total) * 100}%`,
      }}
      onClick={onSelect}
      onDoubleClick={(e) => {
        e.stopPropagation();
        onFocus();
      }}
    >
      {zoom > 6 && wave.data && (
        <div className="dd-clip-wave">
          <Waveform data={wave.data} />
        </div>
      )}
      <span>{segment.segmentId.replace("seg", "")}</span>
      {zoom > 8 && (
        <small>
          {kind === "source" ? segment.sourceText : segment.translation}
        </small>
      )}
    </button>
  );
}

export const TimelineClip = memo(
  TimelineClipView,
  (a, b) =>
    a.segment === b.segment &&
    a.kind === b.kind &&
    a.total === b.total &&
    a.zoom === b.zoom &&
    a.selected === b.selected &&
    a.adapter === b.adapter,
);
