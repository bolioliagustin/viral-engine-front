export interface WhisperWord {
  word: string;
  start: number;
  end: number;
}

export interface WhisperWordsData {
  words: WhisperWord[];
  segments?: unknown[];
}

export interface WordCorrection {
  start: number;
  end: number;
  original: string;
  corrected: string;
}

export type WordStyleType = "default" | "highlight" | "emphasis";

export interface WordStyle {
  start: number;
  end: number;
  style: WordStyleType;
  color?: string;
}

/** Parse whisper_words from API (JSON string or object). */
export function parseWhisperWords(
  raw: string | WhisperWordsData | null | undefined
): WhisperWordsData | null {
  if (!raw) return null;
  try {
    const data = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!data?.words || !Array.isArray(data.words)) return null;
    return {
      words: data.words.map((w: WhisperWord) => ({
        word: String(w.word ?? "").trim(),
        start: Number(w.start ?? 0),
        end: Number(w.end ?? w.start ?? 0),
      })),
      segments: data.segments,
    };
  } catch {
    return null;
  }
}

/** Subtitle time coverage: (last.end - first.start) / clipDuration */
export function computeSubtitleCoverage(
  whisperWords: WhisperWordsData | null,
  clipDuration: number
): number | null {
  if (!whisperWords?.words?.length || clipDuration <= 0) return null;
  const { words } = whisperWords;
  const span = words[words.length - 1].end - words[0].start;
  return Math.min(1, Math.max(0, span / clipDuration));
}

export function isSubtitleCoverageComplete(coverage: number | null): boolean {
  return coverage !== null && coverage >= 0.75;
}

/** Build corrections array from edited texts vs originals. */
export function buildWordCorrections(
  originals: WhisperWord[],
  editedTexts: string[]
): WordCorrection[] {
  const corrections: WordCorrection[] = [];
  for (let i = 0; i < originals.length; i++) {
    const original = originals[i].word;
    const corrected = (editedTexts[i] ?? original).trim();
    if (corrected && corrected !== original) {
      corrections.push({
        start: originals[i].start,
        end: originals[i].end,
        original,
        corrected,
      });
    }
  }
  return corrections;
}

/** Build word_styles for non-default styles only. */
export function buildWordStyles(
  words: WhisperWord[],
  styleMap: Map<string, WordStyleType>
): WordStyle[] {
  const styles: WordStyle[] = [];
  for (const w of words) {
    const key = wordKey(w);
    const style = styleMap.get(key) ?? "default";
    if (style !== "default") {
      styles.push({ start: w.start, end: w.end, style });
    }
  }
  return styles;
}

export function wordKey(w: Pick<WhisperWord, "start" | "end">): string {
  return `${w.start.toFixed(3)}-${w.end.toFixed(3)}`;
}

export function formatWordTimestamp(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  const secStr = s < 10 ? `0${s.toFixed(1)}` : s.toFixed(1);
  return `${m}:${secStr}`;
}
