import { PolicyScreen } from "@/features/store/Policy";
import { policyLabel } from "@/features/store/links";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const label = policyLabel(key);
  return storeMetadata({
    path: label ? `/store/policies/${key}` : "/store/help",
    title: `${label ?? "Policy"} - WorkEasy360 Store`,
    description: label ? `The WorkEasy360 ${label}.` : "WorkEasy360 policies.",
    index: Boolean(label),
  });
}

/** One footer policy, as the Super Admin published it (ADR-065). */
export default async function StorePolicyPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  return <PolicyScreen policyKey={key} />;
}
