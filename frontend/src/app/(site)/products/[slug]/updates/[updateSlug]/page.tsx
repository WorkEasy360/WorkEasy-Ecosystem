"use client";

import { useParams } from "next/navigation";
import { ProductUpdatePage } from "@/features/site/SitePages";

/** `/products/<slug>/updates/<update>` - a published feature or release of a
 * Store product (ADR-054). A draft, archived or unknown one is "not found". */
export default function ProductUpdateRoute() {
  const { slug, updateSlug } = useParams<{ slug: string; updateSlug: string }>();
  return <ProductUpdatePage slug={slug} updateSlug={updateSlug} />;
}
