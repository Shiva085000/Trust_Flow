/**
 * DemoContext — provides demo mode state to the entire component tree.
 *
 * Activation:  append  ?demo=true  to any URL in the app.
 * Deactivation: remove the param or navigate normally.
 *
 * The context manages the single piece of shared state:  demoPhase
 *   "blocked"   — initial BLOCK state (weight mismatch detected)
 *   "resuming"  — 2 s countdown after operator submits correction
 *   "completed" — pipeline resumed, PASS verdict rendered
 */
import React, { createContext, useCallback, useContext, useState } from "react";
import {
  DEMO_RUN_ID,
  buildDemoWorkflowResponse,
  buildDemoStatusResponse,
} from "./mockData";
import type { WorkflowResponse, StatusResponse } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────
export type DemoPhase = "blocked" | "resuming" | "completed";

interface DemoContextValue {
  /** True when ?demo=true is in the URL */
  isDemoMode: boolean;
  /** Current lifecycle phase */
  demoPhase: DemoPhase;
  /** Call when the operator clicks SUBMIT CORRECTIONS */
  submitCorrections: (correctedWeightKg: number) => void;
  /** Pre-built WorkflowResponse list for WorkflowPage */
  demoWorkflows: WorkflowResponse[];
  /** Pre-built StatusResponse for DeclarationPage */
  demoStatus: StatusResponse;
  /** The fixed run ID used for demo navigation */
  demoRunId: string;
}

// ── Context ──────────────────────────────────────────────────────────────────
const DemoContext = createContext<DemoContextValue>({
  isDemoMode:        false,
  demoPhase:         "blocked",
  submitCorrections: () => undefined,
  demoWorkflows:     [],
  demoStatus:        {} as StatusResponse,
  demoRunId:         DEMO_RUN_ID,
});

// ── Provider ─────────────────────────────────────────────────────────────────
export function DemoProvider({ children }: { children: React.ReactNode }) {
  const isDemoMode = new URLSearchParams(window.location.search).get("demo") === "true";
  const [phase, setPhase] = useState<DemoPhase>("blocked");

  const submitCorrections = useCallback((_correctedWeightKg: number) => {
    setPhase("resuming");
    setTimeout(() => setPhase("completed"), 2000);
  }, []);

  // Derive API-shaped data from current phase
  const traceStatus = phase === "completed" ? "completed" : "blocked";
  const demoWorkflows: WorkflowResponse[] = isDemoMode
    ? [buildDemoWorkflowResponse(traceStatus)]
    : [];
  const demoStatus: StatusResponse = buildDemoStatusResponse(traceStatus);

  return (
    <DemoContext.Provider
      value={{
        isDemoMode,
        demoPhase:         phase,
        submitCorrections,
        demoWorkflows,
        demoStatus,
        demoRunId:         DEMO_RUN_ID,
      }}
    >
      {children}
    </DemoContext.Provider>
  );
}

// ── Hook ─────────────────────────────────────────────────────────────────────
export function useDemoMode(): DemoContextValue {
  return useContext(DemoContext);
}
