"use client";

import Link from "next/link";

import { useLayer } from "@/hooks/queries";
import { USE_MOCKS } from "@/lib/env";
import { layerHref, viewerSrc } from "@/lib/layer";

/** The schemas on the left and the ossie-viewer build on the right, opened on one schema's model. */
export function LayerView({ schema, dataset }: { schema: string | null; dataset: string | null }) {
  const layer = useLayer();
  const schemas = layer.data?.schemas ?? [];
  const open = schema && schemas.includes(schema) ? schema : (schemas[0] ?? null);

  return (
    <div className="flex h-full min-h-0 text-[var(--text)]">
      <nav aria-label="Schemas" className="w-[220px] shrink-0 overflow-y-auto border-r px-2 py-3 text-sm">
        <div className="mb-3 flex items-baseline justify-between px-2">
          <h1>Semantic layer</h1>
          <Link href="/" className="text-[var(--muted)] hover:text-[var(--text)]">
            Chat
          </Link>
        </div>
        {USE_MOCKS && <p className="px-2 text-[var(--muted)]">No server is configured, so there is no layer to show.</p>}
        {layer.isError && <p className="px-2 text-[var(--muted)]">The server did not list the layer.</p>}
        <ul className="flex flex-col gap-0.5">
          {schemas.map((name) => (
            <li key={name}>
              <Link
                href={layerHref(name)}
                aria-current={name === open ? "page" : undefined}
                className={`block rounded-md px-2 py-1 hover:bg-[#f6f6f6] ${name === open ? "bg-[#f6f6f6]" : ""}`}
              >
                {name}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {open && (
        <iframe
          key={`${open}/${dataset ?? ""}`}
          title={`The ${open} model in the Ossie viewer`}
          src={viewerSrc(open, open === schema ? dataset : null)}
          className="min-h-0 flex-1 border-0"
        />
      )}
    </div>
  );
}
