"use client";

import { PIPELINE_STEPS, stepIndexForStatus, terminalOffRamp, type PostStatus } from "@/lib/types";

type StepVisual = "done" | "active" | "upcoming" | "branch" | "unreached";

/**
 * One visual state per PIPELINE_STEPS entry, precomputed up front rather than
 * inlined into JSX conditionals -- the off-ramp branching (a post can stop at
 * any step with a failure or removal instead of continuing) makes per-step
 * "is this done / active / upcoming" logic easy to get subtly wrong if it's
 * scattered across the render.
 */
function computeStepVisuals(status: PostStatus): StepVisual[] {
  const offRamp = terminalOffRamp(status);
  const currentIndex = stepIndexForStatus(status);

  return PIPELINE_STEPS.map((_, i) => {
    if (offRamp) {
      if (i < currentIndex) return "done";
      if (i === currentIndex) return "branch";
      return "unreached";
    }
    if (i < currentIndex) return "done";
    if (i === currentIndex) return status === "completed" ? "done" : "active";
    return "upcoming";
  });
}

export default function PostStepper({ status }: { status: PostStatus }) {
  const offRamp = terminalOffRamp(status);
  const currentIndex = stepIndexForStatus(status);
  const visuals = computeStepVisuals(status);
  const stepCount = PIPELINE_STEPS.length;

  // The post travelled through every connector up to and including the
  // branch point itself (a failed_generation post really did pass through
  // "generating"'s connector before failing there) -- so the fill bar's
  // width is driven by currentIndex regardless of whether this is a normal
  // step or an off-ramp.
  const fillPct = (Math.min(currentIndex, stepCount - 1) / (stepCount - 1)) * 100;
  const fillColor = offRamp === "failed_generation" || offRamp === "failed_analysis" ? "var(--err)" : offRamp === "removed" ? "var(--text-faint)" : "var(--ok)";

  return (
    <div className="stepper">
      <div className="stepper-track">
        <div
          className="stepper-track-fill"
          style={{ width: `${fillPct}%`, background: fillColor }}
        />
      </div>
      <div className="stepper-dots">
        {PIPELINE_STEPS.map((step, i) => {
          const visual = visuals[i];
          return (
            <div className="stepper-item" key={step.key}>
              <div
                className={[
                  "stepper-dot",
                  visual,
                  visual === "branch" ? (offRamp === "removed" ? "off-removed" : "off-failed") : "",
                ].join(" ")}
                title={visual === "branch" ? (offRamp === "removed" ? "Removed" : "Failed") : step.label}
              >
                {visual === "done" && "✓"}
                {visual === "branch" && (offRamp === "removed" ? "–" : "✕")}
              </div>
              <div className={`stepper-label ${visual}`}>
                {visual === "branch" ? (offRamp === "removed" ? "Removed" : "Failed") : step.label}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
