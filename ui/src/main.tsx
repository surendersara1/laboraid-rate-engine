import { Amplify } from "aws-amplify";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

interface RuntimeConfig {
  userPoolId: string;
  userPoolClientId: string;
  cognitoDomain: string;
  region: string;
  apiEndpoint: string;
}

async function bootstrap(): Promise<void> {
  const root = createRoot(document.getElementById("root")!);
  root.render(<div className="p-8 text-center">Loading…</div>);

  // Pull Cognito IDs from a runtime config file deployed alongside the SPA so
  // the same JS bundle works against dev/prod stacks without a rebuild.
  let cfg: RuntimeConfig;
  try {
    const resp = await fetch(`/config.json?t=${Date.now()}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    cfg = (await resp.json()) as RuntimeConfig;
  } catch (e) {
    root.render(
      <div className="p-8 text-center text-red-600">
        Failed to load runtime config (/config.json): {String(e)}
      </div>,
    );
    return;
  }

  // Expose the runtime config on window so lib/api.ts can read the API endpoint
  // without having to thread it through React context. The same /config.json
  // serves both Amplify Auth setup and the REST base URL for the API client.
  window.__LABORAID_CONFIG__ = { apiEndpoint: cfg.apiEndpoint };

  const origin = window.location.origin + "/";
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: cfg.userPoolId,
        userPoolClientId: cfg.userPoolClientId,
        loginWith: {
          oauth: {
            domain: `${cfg.cognitoDomain}.auth.${cfg.region}.amazoncognito.com`,
            scopes: ["email", "openid"],
            redirectSignIn: [origin],
            redirectSignOut: [origin],
            responseType: "code",
          },
        },
      },
    },
    API: {
      REST: {
        api: { endpoint: cfg.apiEndpoint, region: cfg.region },
      },
    },
  });

  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
