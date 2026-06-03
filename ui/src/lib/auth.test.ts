import { describe, expect, it } from "vitest";
import { personaForGroups } from "./auth";

describe("personaForGroups", () => {
  it("maps admin groups", () => {
    expect(personaForGroups(["Admins"])).toBe("admin");
    expect(personaForGroups(["Operations"])).toBe("admin");
  });
  it("maps business", () => {
    expect(personaForGroups(["Business"])).toBe("business");
  });
  it("maps both", () => {
    expect(personaForGroups(["Admins", "Business"])).toBe("both");
  });
  it("maps none", () => {
    expect(personaForGroups([])).toBe("none");
  });
});
