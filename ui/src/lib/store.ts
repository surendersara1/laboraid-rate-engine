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
  cellLabel: string;
  currentValue: string;
  value: string;
  justification: string;
  open: (cellId: string, label: string, currentValue: string) => void;
  close: () => void;
  setValue: (v: string) => void;
  setJustification: (v: string) => void;
}

export const useOverrideStore = create<OverrideDraft>((set) => ({
  cellId: null,
  cellLabel: "",
  currentValue: "",
  value: "",
  justification: "",
  open: (cellId, cellLabel, currentValue) =>
    set({ cellId, cellLabel, currentValue, value: currentValue, justification: "" }),
  close: () =>
    set({ cellId: null, cellLabel: "", currentValue: "", value: "", justification: "" }),
  setValue: (value) => set({ value }),
  setJustification: (justification) => set({ justification }),
}));
