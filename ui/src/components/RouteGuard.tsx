import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useUserStore } from "../lib/store";

// Cognito group gate. Renders children only if the user is in one of `groups`,
// otherwise redirects to their allowed landing page (Spec/09 §4 L1 §1.1).
export function RouteGuard({
  groups,
  children,
}: {
  groups: string[];
  children: ReactNode;
}): JSX.Element {
  const userGroups = useUserStore((s) => s.groups);
  const allowed = groups.some((g) => userGroups.includes(g));
  if (!allowed) {
    const fallback = userGroups.includes("Business")
      ? "/business/inbox"
      : "/admin/dashboard";
    return <Navigate to={fallback} replace />;
  }
  return <>{children}</>;
}
