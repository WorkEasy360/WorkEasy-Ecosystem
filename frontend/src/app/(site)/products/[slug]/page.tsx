"use client";

import { useParams } from "next/navigation";
import { ProductPage } from "@/features/site/SitePages";

/** `/products/<slug>` - one catalog product. An unknown, private or withdrawn
 * slug is the same "not found" answer. */
export default function ProductDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  return <ProductPage slug={slug} />;
}
