// Zustand stores: current user + groups, job polling, draft override form.
import { create } from "zustand";
import type { Persona } from "./auth";

interface UserState {
  groups: string[];
  persona: Persona;
  activePersona: "admin" | "business" | null;
  setUser: (groups: string[], persona: Persona) => void;
  setActivePersona: (p: "admin" | "business") => void;
}

export const useUserStore = create<UserState>((set) => ({
  groups: [],
  persona: "none",
  activePersona: null,
  setUser: (groups, persona) =>
    set({
      groups,
      persona,
      activePersona:
        persona === "admin"
          ? "admin"
          : persona === "business"
            ? "business"
            : null,
    }),
  setActivePersona: (activePersona) => set({ activePersona }),
}));

interface OverrideDraft {
  cellId: string | null;
  value: string;
  open: (cellId: string) => void;
  close: () => void;
  setValue: (v: string) => void;
}

export const useOverrideStore = create<OverrideDraft>((set) => ({
  cellId: null,
  value: "",
  open: (cellId) => set({ cellId, value: "" }),
  close: () => set({ cellId: null, value: "" }),
  setValue: (value) => set({ value }),
}));
