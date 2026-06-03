import type { ReactNode } from "react";
import { useUserStore } from "../lib/store";
import { Forbidden403 } from "./Forbidden403";

// Cognito group gate. Renders children only if the user is in one of `groups`,
// otherwise renders an explicit 403 (Spec/09 §4 L1 §1.1: `/admin/*` returns 403
// for Business users). The root-level landing redirect lives in App.tsx; denial
// of an area the user reached directly is a 403, not a silent bounce (audit D4).
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
    return <Forbidden403 />;
  }
  return <>{children}</>;
}
