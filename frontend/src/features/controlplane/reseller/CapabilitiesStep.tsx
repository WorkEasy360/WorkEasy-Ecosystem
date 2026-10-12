"use client";

import { useId, useState } from "react";
import type { CapabilityCatalog } from "@/lib/api/partnerConsole";
import { Segmented } from "../widgets";
import styles from "../ControlPlane.module.css";
import local from "../PartnerConsole.module.css";
import cls from "../AddReseller.module.css";
import { chosenCapabilities, type StepProps } from "./draft";

function Capability({
  capabilityKey,
  catalog,
  checked,
  advanced,
  onChange,
}: {
  capabilityKey: string;
  catalog: CapabilityCatalog;
  checked: boolean;
  advanced: boolean;
  onChange: (on: boolean) => void;
}) {
  const helpId = useId();
  const info = catalog.results.find((c) => c.key === capabilityKey);
  const short = info?.short_label || info?.label || capabilityKey;
  const full = info?.label && info.label !== short ? info.label : "";
  return (
    <li>
      <label>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          aria-describedby={full || advanced ? helpId : undefined}
        />{" "}
        {short}
      </label>
      {(full || advanced) && (
        <p id={helpId} className={cls.help}>
          {full}
          {advanced && (
            <>
              {full ? " " : ""}
              <span className={styles.mono}>{capabilityKey}</span>
            </>
          )}
        </p>
      )}
    </li>
  );
}

/** Step 3: what the partner may do. A preset only ticks existing keys; nothing is free text. */
export function CapabilitiesStep({
  catalog,
  draft,
  set,
  errors,
  preset: presetKey,
}: Pick<StepProps, "draft" | "set" | "errors"> & { catalog: CapabilityCatalog; preset: string | null }) {
  const [advanced, setAdvanced] = useState(false);
  const preset = catalog.presets.find((p) => p.key === presetKey);
  const selected = new Set(chosenCapabilities(draft, catalog, presetKey));
  const usingPreset =
    preset !== undefined && preset.capabilities.length === selected.size && preset.capabilities.every((k) => selected.has(k));
  const errorId = useId();

  function toggle(key: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(key);
    else next.delete(key);
    set("capabilities", [...next]);
  }

  return (
    <>
      <p className={styles.muted}>
        Capabilities are what the partner may do. Choose at least one. A preset only ticks capabilities from this list - nothing is
        created dynamically, and one account can hold any combination.
      </p>
      {preset && (
        <Segmented<"preset" | "custom">
          legend="Start from"
          options={[
            { value: "preset", label: preset.label },
            { value: "custom", label: "Custom" },
          ]}
          value={usingPreset ? "preset" : "custom"}
          onChange={(v) => set("capabilities", v === "preset" ? [...preset.capabilities] : [...selected])}
          hint={preset.description}
        />
      )}
      <label className="w360-label">
        <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} /> Advanced permissions
      </label>
      {advanced && <p className="w360-hint">Showing the exact capability key stored for each choice.</p>}
      {catalog.groups.map((group) => (
        <fieldset key={group.key} className={local.checkGroup} aria-describedby={errors.capabilities ? errorId : undefined}>
          <legend>{group.label}</legend>
          <ul className={local.checks}>
            {group.capabilities.map((key) => (
              <Capability
                key={key}
                capabilityKey={key}
                catalog={catalog}
                checked={selected.has(key)}
                advanced={advanced}
                onChange={(on) => toggle(key, on)}
              />
            ))}
          </ul>
        </fieldset>
      ))}
      <p className={styles.muted}>{selected.size} selected.</p>
      {errors.capabilities && (
        <p id={errorId} className="w360-error-text">
          {errors.capabilities}
        </p>
      )}
    </>
  );
}
