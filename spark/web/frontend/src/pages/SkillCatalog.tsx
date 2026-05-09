import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { ApprovedSkill, PendingSkill } from "../lib/types";

export default function SkillCatalog() {
  const client = useQueryClient();
  const pending = useQuery<PendingSkill[]>({
    queryKey: ["skills-pending"],
    queryFn: () => api.get("/api/skills/pending"),
  });
  const [agent, setAgent] = useState("");
  const approved = useQuery<ApprovedSkill[]>({
    queryKey: ["skills-approved", agent],
    queryFn: () =>
      agent
        ? api.get(`/api/skills/approved/${encodeURIComponent(agent)}`)
        : Promise.resolve([]),
    enabled: !!agent,
  });

  const decide = useMutation({
    mutationFn: (args: {
      review_id: string;
      decision: "approve" | "reject";
      notes?: string;
      final_name?: string;
      final_description?: string;
    }) =>
      api.post(`/api/skills/reviews/${encodeURIComponent(args.review_id)}`, {
        decision: args.decision,
        notes: args.notes,
        final_name: args.final_name,
        final_description: args.final_description,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["skills-pending"] });
      client.invalidateQueries({ queryKey: ["skills-approved"] });
    },
  });

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-2xl font-bold">Skills</h2>
        <p className="text-spark-muted text-sm">
          Agent skills awaiting review. <em>API</em> skills come from the
          discovery engine crawling docs; <em>behavior</em> and{" "}
          <em>knowledge</em> skills come from agents themselves via the{" "}
          <code>propose_skill</code> plugin. Approve to register, reject to
          discard.
        </p>
      </header>

      <PendingPanel
        pending={pending.data ?? []}
        onApprove={(p, final_name, final_description, notes) =>
          decide.mutate({
            review_id: p.review_id,
            decision: "approve",
            final_name,
            final_description,
            notes,
          })
        }
        onReject={(p, notes) =>
          decide.mutate({
            review_id: p.review_id,
            decision: "reject",
            notes,
          })
        }
      />

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Approved skills</h3>
        <input
          className="input mb-3 w-80"
          placeholder="agent name"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        />
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">name</th>
              <th className="text-left">service</th>
              <th className="text-left">auth</th>
              <th className="text-left">hosts</th>
              <th className="text-left">secrets</th>
              <th className="text-left">uses</th>
              <th className="text-left">status</th>
            </tr>
          </thead>
          <tbody>
            {(approved.data ?? []).map((s) => (
              <tr key={s.skill_id} className="border-t border-spark-border">
                <td className="py-1 font-mono">{s.name}</td>
                <td>{s.service_name}</td>
                <td>{s.auth_method}</td>
                <td className="text-xs">{s.required_hosts.join(", ")}</td>
                <td className="text-xs">{s.required_secrets.join(", ")}</td>
                <td>{s.uses}</td>
                <td>
                  <span className={`chip ${s.status === "approved" ? "chip-good" : ""}`}>
                    {s.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

type KindFilter = "all" | "api" | "behavior" | "knowledge";

function PendingPanel({
  pending,
  onApprove,
  onReject,
}: {
  pending: PendingSkill[];
  onApprove: (
    p: PendingSkill,
    final_name: string,
    final_description: string,
    notes: string,
  ) => void;
  onReject: (p: PendingSkill, notes: string) => void;
}) {
  const [filter, setFilter] = useState<KindFilter>("all");

  const counts: Record<KindFilter, number> = {
    all: pending.length,
    api: pending.filter((p) => (p.kind ?? "api") === "api").length,
    behavior: pending.filter((p) => p.kind === "behavior").length,
    knowledge: pending.filter((p) => p.kind === "knowledge").length,
  };

  const visible = pending.filter((p) =>
    filter === "all" ? true : (p.kind ?? "api") === filter,
  );

  return (
    <section className="panel p-4">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 className="font-semibold">Pending review ({pending.length})</h3>
        <div className="flex gap-1 text-xs">
          {(["all", "api", "behavior", "knowledge"] as KindFilter[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilter(k)}
              className={`px-2 py-1 rounded-md border ${
                filter === k
                  ? "border-spark-link text-spark-text bg-spark-border/30"
                  : "border-spark-border text-spark-muted hover:text-spark-text"
              }`}
            >
              {k} ({counts[k]})
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-3">
        {visible.map((p) => (
          <PendingCard
            key={p.review_id}
            skill={p}
            onApprove={(final_name, final_description, notes) =>
              onApprove(p, final_name, final_description, notes)
            }
            onReject={(notes) => onReject(p, notes)}
          />
        ))}
        {visible.length === 0 && (
          <div className="text-spark-muted text-sm">
            {pending.length === 0
              ? "No pending reviews."
              : `No ${filter} skills pending. Switch the filter to see others.`}
          </div>
        )}
      </div>
    </section>
  );
}

function PendingCard({
  skill,
  onApprove,
  onReject,
}: {
  skill: PendingSkill;
  onApprove: (name: string, description: string, notes: string) => void;
  onReject: (notes: string) => void;
}) {
  const [name, setName] = useState(skill.proposed_name);
  const [description, setDescription] = useState(skill.proposed_description);
  const [notes, setNotes] = useState("");
  const kind = skill.kind ?? "api";
  const isApi = kind === "api";

  const kindChipClass =
    kind === "api"
      ? "chip-good"
      : kind === "behavior"
        ? "chip-warn"
        : "";

  return (
    <div className="border border-spark-border rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="chip chip-warn">pending</span>
          <span className={`chip ${kindChipClass}`}>{kind}</span>
          <span className="font-semibold">
            {isApi ? skill.service_name : skill.proposed_name}
          </span>
          <span className="text-spark-muted text-xs">
            from {skill.agent_name}
          </span>
          <span className="text-spark-muted text-xs">
            confidence {(skill.confidence * 100).toFixed(0)}%
          </span>
        </div>
        {skill.source_url && skill.source_url.startsWith("http") && (
          <a
            href={skill.source_url}
            target="_blank"
            rel="noreferrer"
            className="text-spark-muted text-xs underline"
          >
            source
          </a>
        )}
      </div>

      {skill.rationale && (
        <div className="border-l-2 border-spark-border pl-2 text-sm">
          <span className="label block">Rationale</span>
          <span className="text-spark-text">{skill.rationale}</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 text-sm">
        <label>
          <span className="label">Skill name</span>
          <input
            className="input w-full"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {isApi ? (
          <label>
            <span className="label">Base URL</span>
            <input
              className="input w-full font-mono text-xs"
              value={skill.base_url}
              readOnly
            />
          </label>
        ) : (
          <div>
            <span className="label">Kind</span>
            <div className="text-xs text-spark-muted">
              {kind === "behavior"
                ? "How-to-think heuristic. No external service."
                : "Domain rule / fact. Surfaced via long-term memory after approval."}
            </div>
          </div>
        )}
      </div>

      <label className="block">
        <span className="label">Description</span>
        <textarea
          className="input w-full h-16"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>

      {skill.examples && skill.examples.length > 0 && (
        <div>
          <span className="label">Examples ({skill.examples.length})</span>
          <ul className="list-disc pl-5 text-xs text-spark-muted space-y-0.5">
            {skill.examples.slice(0, 5).map((ex, i) => (
              <li key={i}>{ex}</li>
            ))}
          </ul>
        </div>
      )}

      {skill.success_criteria && (
        <div className="text-xs">
          <span className="label">Success criteria</span>
          <div className="text-spark-muted">{skill.success_criteria}</div>
        </div>
      )}

      {isApi && (
        <div className="text-xs text-spark-muted grid grid-cols-2 gap-2">
          <div>
            <span className="label">required hosts</span>
            <div className="font-mono">
              {skill.required_hosts.join(", ") || "—"}
            </div>
          </div>
          <div>
            <span className="label">required secrets</span>
            <div className="font-mono">
              {skill.required_secrets.join(", ") || "—"}
            </div>
          </div>
        </div>
      )}

      <label className="block">
        <span className="label">Review notes</span>
        <input
          className="input w-full"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </label>
      <div className="flex gap-2 justify-end">
        <button className="btn btn-danger" onClick={() => onReject(notes)}>
          Reject
        </button>
        <button
          className="btn btn-primary"
          onClick={() => onApprove(name, description, notes)}
        >
          Approve
        </button>
      </div>
    </div>
  );
}
