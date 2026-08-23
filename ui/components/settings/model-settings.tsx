"use client";

/**
 * `/settings` — the three model surfaces this engine is actually running on.
 *
 * **Read from `/capabilities`, which reads `knobs_resolved`.** That is the same mapping every
 * measurement row publishes, so what this page shows is the identity a run is *recorded* under
 * rather than a second derivation that could drift from it. A settings page built off the client
 * objects instead would have shown `us.anthropic.claude-sonnet-5` while the artifact said
 * `amazon_bedrock_converse_chat`, which is exactly the bug this page was built on top of
 * (`serve/runtime.py::model_id`).
 *
 * It is read-only on purpose. `register/knobs.py` is the one home for a knob's value and `.env`
 * is how an operator sets one; a form here would be a second place deciding a knob, which is the
 * defect AGENTS.md names. So this reports, and says where to change it.
 */

import { Boxes, Braces, Cpu, Database, Ruler } from "lucide-react";

import { useCapabilities } from "@/hooks/queries";
import type { Capabilities } from "@/lib/types";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

/** A value the engine did not report. Distinct from a value it reported as absent. */
function NotReported({ what = "not reported" }: { what?: string }) {
  return <span className="text-muted-foreground italic">{what}</span>;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-right text-sm font-medium tabular-nums">{children}</span>
    </div>
  );
}

function SurfaceCard({
  icon,
  title,
  what,
  id,
  provider,
  extra,
}: {
  icon: React.ReactNode;
  title: string;
  what: string;
  id: string | null | undefined;
  provider: string | null | undefined;
  extra?: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <p className="pb-2 text-xs text-muted-foreground">{what}</p>
      <Row label="Model">
        {/* `break-all`: Bedrock ids are long and dotted and must not be truncated —
            a half-shown model id is worse than a wrapped one. */}
        {id ? <code className="break-all text-xs">{id}</code> : <NotReported />}
      </Row>
      <Row label="Gateway">
        {provider ? <Badge variant="secondary">{provider}</Badge> : <NotReported />}
      </Row>
      {extra}
    </Card>
  );
}

/**
 * The warehouse this engine is pointed at. Moved here from the nav rail, which had room for
 * `local · postgres` and nothing else — and "postgres" alone does not tell you *which* postgres,
 * which is the only part of it anyone needs when two warehouses are in play.
 *
 * No credential is rendered because none arrives: the connector never parses `user` or
 * `password` out of the DSN, so there is no field here to leak. `host`/`port` render as a
 * dash rather than as empty space when absent, which happens because `connection_for`
 * copies only what the connector's `endpoint` mapping carries — not because a fileless
 * dialect is served here. `Environment` renders the wire value verbatim, and there is
 * exactly one: the literal "local".
 */
function ConnectionCard({ caps }: { caps: Capabilities }) {
  const conn = caps.connection;
  return (
    <Card className="flex flex-col gap-1 p-4">
      <div className="flex items-center gap-2">
        <Database className="size-4" />
        <h3 className="text-sm font-semibold">Database</h3>
      </div>
      <p className="pb-2 text-xs text-muted-foreground">
        The warehouse every governed query executes against.
      </p>
      <Row label="Environment">{caps.environment}</Row>
      <Row label="Dialect">
        <Badge variant="secondary">{conn?.dialect ?? caps.dialect}</Badge>
      </Row>
      <Row label="Database">
        {conn?.database ? <code className="text-xs">{conn.database}</code> : <NotReported />}
      </Row>
      <Row label="Host">
        {conn?.host ? (
          <code className="text-xs">
            {conn.host}
            {conn.port ? `:${conn.port}` : ""}
          </code>
        ) : (
          <NotReported what="—" />
        )}
      </Row>
    </Card>
  );
}

export function ModelSettings() {
  const query = useCapabilities();

  return (
    <QueryState<Capabilities> query={query}>
      {(caps) => {
        const models = caps.models;
        if (!models) {
          return (
            <p className="text-sm text-muted-foreground">
              This engine does not report its models on <code>/capabilities</code>. That field
              arrived with the settings page; an older server omits it.
            </p>
          );
        }
        return (
          <div className="flex flex-col gap-6">
            <div className="grid gap-4 md:grid-cols-3">
              <SurfaceCard
                icon={<Cpu className="size-4" />}
                title="Agent model"
                what="Writes the SQL and drives the tool loop."
                id={models.agent.id}
                provider={models.agent.provider}
                extra={
                  <Row label="Effort">
                    {models.agent.effort ? (
                      <Badge>{models.agent.effort}</Badge>
                    ) : (
                      <NotReported what="default" />
                    )}
                  </Row>
                }
              />
              <SurfaceCard
                icon={<Braces className="size-4" />}
                title="Utility model"
                what="The scope gate and the facet rewriters — six short calls per turn."
                id={models.utility.id}
                provider={models.utility.provider}
                extra={
                  <Row label="Effort">
                    {models.utility.effort ? (
                      <Badge>{models.utility.effort}</Badge>
                    ) : (
                      <NotReported what="not recorded" />
                    )}
                  </Row>
                }
              />
              <SurfaceCard
                icon={<Boxes className="size-4" />}
                title="Embedding model"
                what="The vector space retrieval searches in."
                id={models.embedding.id}
                provider={models.embedding.provider}
                extra={
                  <Row label="Width">
                    {models.embedding.dimensions !== null &&
                    models.embedding.dimensions !== undefined ? (
                      <span className="inline-flex items-center gap-1">
                        <Ruler className="size-3.5 text-muted-foreground" />
                        {models.embedding.dimensions} dims
                      </span>
                    ) : (
                      <NotReported />
                    )}
                  </Row>
                }
              />
            </div>

            <ConnectionCard caps={caps} />

            <div className="space-y-2 text-xs text-muted-foreground">
              <p>
                Read-only. Models are set per surface in the engine&apos;s environment
                (<code>GOVERNED_BI_MODEL</code>, <code>GOVERNED_BI_UTILITY_MODEL</code>,
                <code> GOVERNED_BI_EMBEDDING_MODEL</code>) and their defaults are declared in the
                knob register. This page reports what the running engine resolved; it does not set
                it, because a second place deciding a knob is how two of them come to disagree.
              </p>
              <p>
                The embedding id carries its gateway (<code>bedrock:…</code>) because the vector
                cache is keyed on model, width and text — two gateways serving one nominal id must
                not share cached vectors. Changing the model or the width starts a new vector
                space, so retrieval figures either side of a change are not comparable.
              </p>
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}
