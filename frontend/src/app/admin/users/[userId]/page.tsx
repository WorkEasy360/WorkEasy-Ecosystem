"use client";

import { useParams } from "next/navigation";
import { UserDetailScreen } from "@/features/controlplane/AdminUsers";

export default function Page() {
  const { userId } = useParams<{ userId: string }>();
  return <UserDetailScreen userId={userId} />;
}
