"use client";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import {
  TASK_ORDER,
  formatUsd,
  shortModel,
  type PipelineGroup,
  type UsageEvent,
} from "@/lib/admin-usage";
import { ChevronDown } from "lucide-react";

interface JobPipelineViewProps {
  eventsGrouped: Record<string, PipelineGroup>;
}

function EventRow({ e }: { e: UsageEvent }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-xs border-b border-white/5 last:border-0">
      <div className="flex items-center gap-2 min-w-0">
        {e.moment_index != null && (
          <span className="text-slate-600 font-mono shrink-0">m{e.moment_index}</span>
        )}
        <span className="text-slate-400 font-mono truncate">{shortModel(e.model)}</span>
        {e.cache_hit && (
          <Badge variant="outline" className="text-[9px] border-emerald-500/30 text-emerald-400 py-0">
            cache
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-3 shrink-0 font-mono text-slate-500">
        {e.task === "whisper" ? (
          <span>{Number(e.audio_seconds || 0).toFixed(1)}s</span>
        ) : (
          <span>
            {e.input_tokens}/{e.output_tokens}
            {e.reasoning_tokens ? `+${e.reasoning_tokens}r` : ""}
          </span>
        )}
        <span className="text-purple-300 w-16 text-right">
          {formatUsd(Number(e.estimated_cost_usd), 6)}
        </span>
      </div>
    </div>
  );
}

function TaskSection({ task, group }: { task: string; group: PipelineGroup }) {
  const hasMoments = Object.keys(group.by_moment).length > 0;

  return (
    <Collapsible defaultOpen={task === "analysis" || task === "copy"}>
      <CollapsibleTrigger className="w-full flex items-center justify-between gap-3 px-4 py-3 hover:bg-white/5 rounded-lg transition-colors group">
        <div className="flex items-center gap-3">
          <ChevronDown className="w-4 h-4 text-slate-500 transition-transform group-data-[state=open]:rotate-180" />
          <span className="text-sm font-medium text-white">{group.label}</span>
          <Badge variant="outline" className="text-[10px] border-white/10 text-slate-400">
            {group.event_count} evento{group.event_count !== 1 ? "s" : ""}
          </Badge>
        </div>
        <span className="text-sm font-mono text-purple-300">
          {formatUsd(group.total_cost_usd)}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="px-4 pb-3 pl-10">
        {group.events.map((e) => (
          <EventRow key={e.id} e={e} />
        ))}
        {hasMoments &&
          Object.entries(group.by_moment)
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([moment, evts]) => (
              <div key={moment} className="mt-2">
                <p className="text-[10px] text-slate-600 uppercase tracking-wider mb-1">
                  Momento {moment}
                </p>
                {evts.map((e) => (
                  <EventRow key={e.id} e={e} />
                ))}
              </div>
            ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

export function JobPipelineView({ eventsGrouped }: JobPipelineViewProps) {
  const ordered = TASK_ORDER.filter((t) => eventsGrouped[t]);
  const extra = Object.keys(eventsGrouped).filter((t) => !TASK_ORDER.includes(t));

  if (!ordered.length && !extra.length) {
    return (
      <p className="text-sm text-slate-500 py-6 text-center">
        Sin eventos registrados para este job.
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/60 divide-y divide-white/5">
      {[...ordered, ...extra].map((task) => (
        <TaskSection key={task} task={task} group={eventsGrouped[task]} />
      ))}
    </div>
  );
}
