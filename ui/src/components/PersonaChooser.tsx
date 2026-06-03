import { useNavigate } from "react-router-dom";
import { useUserStore } from "../lib/store";

// Shown at "/" for users in both Admins and Business (Spec/09 §4 L1 §1.1).
export function PersonaChooser(): JSX.Element {
  const navigate = useNavigate();
  const setActive = useUserStore((s) => s.setActivePersona);

  const pick = (p: "admin" | "business") => {
    setActive(p);
    navigate(p === "admin" ? "/admin/dashboard" : "/business/inbox");
  };

  return (
    <div className="flex min-h-screen items-center justify-center gap-6">
      <button
        className="rounded-lg bg-brand px-8 py-6 text-lg text-white"
        onClick={() => pick("admin")}
      >
        Admin / Operations
      </button>
      <button
        className="rounded-lg bg-brand px-8 py-6 text-lg text-white"
        onClick={() => pick("business")}
      >
        Business Review
      </button>
    </div>
  );
}
