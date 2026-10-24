"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { LayerView } from "@/components/layer/layer-view";

function FromSearch() {
  const search = useSearchParams();
  return <LayerView schema={search.get("schema")} dataset={search.get("dataset")} />;
}

export default function LayerPage() {
  return (
    <main className="h-full bg-white">
      <Suspense>
        <FromSearch />
      </Suspense>
    </main>
  );
}
