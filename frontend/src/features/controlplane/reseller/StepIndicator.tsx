"use client";

import cls from "../AddReseller.module.css";
import { STEPS, STEP_LABELS, type StepKey } from "./draft";

/**
 * The wizard's progress: an ordered list with the current step marked
 * `aria-current="step"`. On a narrow screen only the current step keeps its name
 * on screen (the others shrink to their number, their names stay available to
 * assistive technology), so nine steps never force a wide page. A step already
 * passed can be reopened; one not yet reached cannot be jumped to.
 */
export function StepIndicator({
  step,
  onJump,
  locked,
}: {
  step: StepKey;
  onJump: (step: StepKey) => void;
  /** Once the partner exists the steps can no longer be reopened. */
  locked: boolean;
}) {
  const current = STEPS.indexOf(step);
  return (
    <nav aria-label="Setup steps">
      <ol className={cls.steps}>
        {STEPS.map((key, i) => {
          const label = (
            <>
              <span aria-hidden="true">{i + 1}.</span> <span className={cls.stepLabel}>{STEP_LABELS[key]}</span>
            </>
          );
          return (
            <li key={key} className={cls.step} aria-current={key === step ? "step" : undefined}>
              {i < current && !locked ? (
                <button type="button" className={cls.stepButton} onClick={() => onJump(key)}>
                  {label}
                  <span className="w360-visually-hidden"> (completed - open to change)</span>
                </button>
              ) : (
                label
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
