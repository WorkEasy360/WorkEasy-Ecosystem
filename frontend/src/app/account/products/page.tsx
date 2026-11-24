import { notFound } from "next/navigation";
import { CenterProducts } from "@/features/account/CenterProducts";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("My Products"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: every product of every organization the person belongs to. */
export default function AccountProductsPage() {
  if (!centerOrigins()) notFound();
  return <CenterProducts />;
}
