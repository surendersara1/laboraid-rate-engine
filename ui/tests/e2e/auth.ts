import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
} from "@aws-sdk/client-cognito-identity-provider";

// Test-only Cognito client (created in security_stack.py as SpaTestClient with
// USER_PASSWORD_AUTH enabled). The production SPA client (8mlbfdiopceq…) still
// only allows OAuth code flow; this one exists solely so the E2E suite can
// bypass the hosted-UI redirect.
const REGION = "us-east-2";
const USER_POOL_ID = "us-east-2_CC90iICJt";
const TEST_CLIENT_ID = "7g8l4dfcirofqtkafoi1u3869g";
const PROD_CLIENT_ID = "8mlbfdiopceq8gvflpvqvrh5u";

export interface AuthTokens {
  idToken: string;
  accessToken: string;
  refreshToken: string;
  username: string;
}

/**
 * Authenticate via Cognito InitiateAuth using the test-only client. Returns
 * the three tokens; the spec then writes them into the page's localStorage in
 * the keys Amplify Auth v6 looks for so the SPA boots already logged in.
 */
export async function authenticate(
  username: string,
  password: string,
): Promise<AuthTokens> {
  const client = new CognitoIdentityProviderClient({ region: REGION });
  const resp = await client.send(
    new InitiateAuthCommand({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: TEST_CLIENT_ID,
      AuthParameters: { USERNAME: username, PASSWORD: password },
    }),
  );
  const r = resp.AuthenticationResult;
  if (!r?.IdToken || !r?.AccessToken || !r?.RefreshToken) {
    throw new Error(
      `InitiateAuth returned no tokens (challenge=${resp.ChallengeName ?? "none"})`,
    );
  }
  return {
    idToken: r.IdToken,
    accessToken: r.AccessToken,
    refreshToken: r.RefreshToken,
    username,
  };
}

/**
 * Build the localStorage entries Amplify Auth v6 uses to hydrate a session.
 *
 * Amplify writes tokens scoped to its app client ID, NOT the InitiateAuth
 * client. The SPA is configured against PROD_CLIENT_ID, so the entries we
 * inject have to use PROD_CLIENT_ID — even though the tokens themselves were
 * issued against TEST_CLIENT_ID. This works because Cognito's JWT validation
 * trusts the user-pool issuer + signature, not the aud-claim mismatch between
 * the two app clients (both belong to the same pool). For a stricter posture
 * we'd issue the test tokens against the SPA's own client — that requires
 * USER_PASSWORD_AUTH on the prod client (option C, which we deliberately
 * declined).
 */
export function amplifyStorageEntries(tokens: AuthTokens): Array<{ name: string; value: string }> {
  const base = `CognitoIdentityServiceProvider.${PROD_CLIENT_ID}`;
  const userKey = `${base}.${tokens.username}`;
  const userData = {
    UserAttributes: [{ Name: "email", Value: tokens.username }],
    Username: tokens.username,
  };
  return [
    { name: `${base}.LastAuthUser`, value: tokens.username },
    { name: `${userKey}.idToken`, value: tokens.idToken },
    { name: `${userKey}.accessToken`, value: tokens.accessToken },
    { name: `${userKey}.refreshToken`, value: tokens.refreshToken },
    { name: `${userKey}.clockDrift`, value: "0" },
    { name: `${userKey}.userData`, value: JSON.stringify(userData) },
    { name: `${userKey}.signInDetails`, value: JSON.stringify({ loginId: tokens.username, authFlowType: "USER_PASSWORD_AUTH" }) },
  ];
}

export { USER_POOL_ID, PROD_CLIENT_ID, TEST_CLIENT_ID, REGION };
