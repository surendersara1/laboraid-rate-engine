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
