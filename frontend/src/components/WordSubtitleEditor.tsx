"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  type WhisperWord,
  type WordCorrection,
  type WordStyle,
  type WordStyleType,
  wordKey,
  formatWordTimestamp,
} from "@/types/subtitles";

const STYLE_OPTIONS: { id: WordStyleType; label: string; className: string }[] = [
  { id: "default", label: "Normal", className: "text-slate-200" },
  { id: "highlight", label: "Destacada", className: "text-yellow-300 font-semibold" },
  { id: "emphasis", label: "Énfasis", className: "text-pink-400 font-bold uppercase" },
];

interface WordSubtitleEditorProps {
  words: WhisperWord[];
  clipUrl?: string;
  clipDuration?: number;
  disabled?: boolean;
  initialCorrections?: WordCorrection[];
  initialStyles?: WordStyle[];
  onChange?: (corrections: WordCorrection[], styles: WordStyle[]) => void;
}

export function WordSubtitleEditor({
  words,
  clipUrl,
  clipDuration,
  disabled = false,
  initialCorrections = [],
  initialStyles = [],
  onChange,
}: WordSubtitleEditorProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [editedTexts, setEditedTexts] = useState<string[]>(() =>
    words.map((w) => w.word)
  );
  const [styleMap, setStyleMap] = useState<Map<string, WordStyleType>>(() => {
    const map = new Map<string, WordStyleType>();
    for (const s of initialStyles) {
      map.set(wordKey(s), s.style);
    }
    return map;
  });

  // Re-init when words or loaded edit changes
  useEffect(() => {
    const correctionByKey = new Map(
      initialCorrections.map((c) => [wordKey(c), c.corrected])
    );
    setEditedTexts(
      words.map((w) => correctionByKey.get(wordKey(w)) ?? w.word)
    );
    const map = new Map<string, WordStyleType>();
    for (const s of initialStyles) {
      map.set(wordKey(s), s.style);
    }
    setStyleMap(map);
  }, [words, initialCorrections, initialStyles]);

  const activeIndex = useMemo(() => {
    if (!words.length) return -1;
    return words.findIndex(
      (w) => currentTime >= w.start && currentTime < w.end
    );
  }, [words, currentTime]);

  const emitChange = useCallback(
    (texts: string[], styles: Map<string, WordStyleType>) => {
      if (!onChange) return;
      const corrections: WordCorrection[] = [];
      for (let i = 0; i < words.length; i++) {
        const original = words[i].word;
        const corrected = (texts[i] ?? original).trim();
        if (corrected && corrected !== original) {
          corrections.push({
            start: words[i].start,
            end: words[i].end,
            original,
            corrected,
          });
        }
      }
      const wordStyles: WordStyle[] = [];
      for (const w of words) {
        const key = wordKey(w);
        const style = styles.get(key) ?? "default";
        if (style !== "default") {
          wordStyles.push({ start: w.start, end: w.end, style });
        }
      }
      onChange(corrections, wordStyles);
    },
    [onChange, words]
  );

  const handleTextChange = (index: number, value: string) => {
    const next = [...editedTexts];
    next[index] = value;
    setEditedTexts(next);
    emitChange(next, styleMap);
  };

  const handleStyleChange = (w: WhisperWord, style: WordStyleType) => {
    const key = wordKey(w);
    const next = new Map(styleMap);
    if (style === "default") {
      next.delete(key);
    } else {
      next.set(key, style);
    }
    setStyleMap(next);
    emitChange(editedTexts, next);
  };

  const changedCount = useMemo(
    () =>
      words.filter((w, i) => (editedTexts[i] ?? w.word).trim() !== w.word)
        .length,
    [words, editedTexts]
  );

  const styledCount = useMemo(() => styleMap.size, [styleMap]);

  if (!words.length) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4 text-sm text-slate-500">
        No hay palabras transcritas para este clip.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {clipUrl && (
        <div className="rounded-lg overflow-hidden border border-slate-800 bg-black">
          <video
            ref={videoRef}
            src={clipUrl}
            controls
            playsInline
            className="w-full max-h-36 object-contain"
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            onSeeked={(e) => setCurrentTime(e.currentTarget.currentTime)}
          />
          <p className="text-[10px] text-slate-500 px-2 py-1 border-t border-slate-800">
            La palabra activa se resalta según el tiempo del video.
          </p>
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {changedCount > 0 && (
          <Badge
            variant="outline"
            className="text-[10px] border-amber-500/30 text-amber-300 bg-amber-500/10"
          >
            {changedCount} corrección{changedCount !== 1 ? "es" : ""}
          </Badge>
        )}
        {styledCount > 0 && (
          <Badge
            variant="outline"
            className="text-[10px] border-pink-500/30 text-pink-300 bg-pink-500/10"
          >
            {styledCount} con estilo
          </Badge>
        )}
        <span className="text-[10px] text-slate-500 ml-auto">
          {words.length} palabras
          {clipDuration ? ` · ${clipDuration.toFixed(0)}s clip` : ""}
        </span>
      </div>

      <div className="max-h-64 overflow-y-auto custom-scrollbar space-y-1.5 pr-1">
        {words.map((w, index) => {
          const key = wordKey(w);
          const style = styleMap.get(key) ?? "default";
          const isActive = index === activeIndex;
          const isChanged = (editedTexts[index] ?? w.word).trim() !== w.word;

          return (
            <div
              key={key}
              className={cn(
                "flex items-center gap-2 p-2 rounded-lg border transition-colors",
                isActive
                  ? "border-purple-500/50 bg-purple-500/10"
                  : "border-slate-800 bg-slate-900/40",
                isChanged && !isActive && "border-amber-500/20"
              )}
            >
              <span className="text-[10px] font-mono text-slate-500 w-16 shrink-0 tabular-nums">
                {formatWordTimestamp(w.start)}
              </span>
              <Input
                value={editedTexts[index] ?? ""}
                onChange={(e) => handleTextChange(index, e.target.value)}
                disabled={disabled}
                className={cn(
                  "h-8 text-sm bg-slate-950 border-slate-700 flex-1 min-w-0",
                  STYLE_OPTIONS.find((s) => s.id === style)?.className
                )}
              />
              <select
                value={style}
                onChange={(e) =>
                  handleStyleChange(w, e.target.value as WordStyleType)
                }
                disabled={disabled}
                className="h-8 text-[10px] bg-slate-950 border border-slate-700 rounded-md px-1.5 text-slate-300 shrink-0 max-w-[5.5rem]"
                title="Estilo de palabra"
              >
                {STYLE_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
      </div>
    </div>
  );
}
