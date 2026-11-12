import { ProductDetailsScreen } from "@/features/store/ProductDetails";
import { storeMetadata } from "@/lib/storeMeta";

const SLUG = /^[a-z0-9][a-z0-9-]{0,79}$/;

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const safe = SLUG.test(slug) ? slug : "";
  const name = safe
    .split("-")
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
  return storeMetadata({
    path: safe ? `/store/product/${safe}` : "/store/products",
    title: name ? `${name} - WorkEasy360 Store` : "Product - WorkEasy360 Store",
    description: name ? `${name}: features, plans and prices in the WorkEasy360 Store.` : "A WorkEasy360 product.",
  });
}

/** `/store/product/<slug>` - the address every Store deep link has always used (ADR-065). */
export default async function StoreProductPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ProductDetailsScreen slug={slug} />;
}
