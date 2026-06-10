// Cognito auth wrappers (Amplify v6). Spec/09 §4 L1 §2.5.
import {
  fetchAuthSession,
  getCurrentUser,
  signInWithRedirect,
  signOut as amplifySignOut,
} from "aws-amplify/auth";

export type Persona = "admin" | "business" | "both" | "none";

export async function getGroups(): Promise<string[]> {
  try {
    const session = await fetchAuthSession();
    const claims = session.tokens?.idToken?.payload ?? {};
    const groups = claims["cognito:groups"];
    return Array.isArray(groups) ? (groups as string[]) : [];
  } catch {
    return [];
  }
}

export async function getJwt(): Promise<string | null> {
  const session = await fetchAuthSession();
  return session.tokens?.idToken?.toString() ?? null;
}

// The string the backend writes into reviewed_by/approved_by — must match the
// _actor() resolution in the approve Lambda (email > cognito:username > sub).
// Used by the UI to enforce dual-control: a user who reviewed a sheet must NOT
// see an enabled Approve button on the same sheet (SOP §6).
export async function getCurrentActor(): Promise<string | null> {
  try {
    const session = await fetchAuthSession();
    const c: Record<string, unknown> = session.tokens?.idToken?.payload ?? {};
    return (
      (c["email"] as string | undefined) ||
      (c["cognito:username"] as string | undefined) ||
      (c["sub"] as string | undefined) ||
      null
    );
  } catch {
    return null;
  }
}

export async function isAuthenticated(): Promise<boolean> {
  try {
    await getCurrentUser();
    return true;
  } catch {
    return false;
  }
}

export function personaForGroups(groups: string[]): Persona {
  const admin = groups.includes("Admins") || groups.includes("Operations");
  const business = groups.includes("Business");
  if (admin && business) return "both";
  if (admin) return "admin";
  if (business) return "business";
  return "none";
}

export async function login(): Promise<void> {
  await signInWithRedirect();
}

export async function logout(): Promise<void> {
  await amplifySignOut();
}
