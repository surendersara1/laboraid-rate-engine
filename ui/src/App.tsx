import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { PersonaChooser } from "./components/PersonaChooser";
import { getGroups, isAuthenticated, login, personaForGroups } from "./lib/auth";
import { useUserStore } from "./lib/store";
import { AppRoutes } from "./routes";

export function App(): JSX.Element {
  const [ready, setReady] = useState(false);
  const setUser = useUserStore((s) => s.setUser);
  const persona = useUserStore((s) => s.persona);

  useEffect(() => {
    // If no session yet, kick straight to Cognito Hosted UI so the user can
    // log in. Without this the app would land on /admin/dashboard with an
    // empty persona and render 403 with no way back to a sign-in screen.
    isAuthenticated().then((authed) => {
      if (!authed) {
        void login();
        return;
      }
      getGroups().then((groups) => {
        setUser(groups, personaForGroups(groups));
        setReady(true);
      });
    });
  }, [setUser]);

  if (!ready) return <div className="p-8">Signing in…</div>;

  const landing =
    persona === "business" ? "/business/inbox" : "/admin/dashboard";

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={
            persona === "both" ? (
              <PersonaChooser />
            ) : (
              <Navigate to={landing} replace />
            )
          }
        />
        <Route path="/*" element={<AppRoutes />} />
      </Routes>
    </BrowserRouter>
  );
}
