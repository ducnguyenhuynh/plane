/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
// plane imports
import { AGENT_ROLES } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { Dialog, DialogContent } from "@makeplane/propel/components/dialog";
import { InputField } from "@makeplane/propel/components/input-field";
import { SelectField } from "@makeplane/propel/components/select-field";
import { Switch } from "@makeplane/propel/components/switch";
import { setToast } from "@plane/blocks/toast";
import type { TAgent, TAgentRole, TProjectAgent } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

type Props = {
  workspaceSlug: string;
  projectId: string;
  agents: TAgent[];
  /** `null` assigns a new agent, an assignment edits it, `undefined` keeps the dialog closed. */
  assignment?: TProjectAgent | null;
  isOpen: boolean;
  onClose: () => void;
};

export function AssignAgentDialog({ workspaceSlug, projectId, agents, assignment, isOpen, onClose }: Props) {
  const [agentId, setAgentId] = useState<string | null>(null);
  const [role, setRole] = useState<TAgentRole>("developer");
  const [modelOverride, setModelOverride] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const agentOptions = useMemo(
    () => agents.map((agent) => ({ value: agent.id, label: `${agent.name} (${agent.model})` })),
    [agents]
  );

  useEffect(() => {
    if (!isOpen) return;
    setAgentId(assignment?.agent.id ?? agents[0]?.id ?? null);
    setRole(assignment?.role ?? "developer");
    setModelOverride(assignment?.model_override ?? "");
    setIsActive(assignment?.is_active ?? true);
  }, [isOpen, assignment, agents]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!agentId) {
      setToast({ type: "error", title: "Missing agent", message: "Create an agent in workspace settings first." });
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = { agent_id: agentId, role, model_override: modelOverride, is_active: isActive };
      if (assignment) await agentService.updateProjectAgent(workspaceSlug, projectId, assignment.id, payload);
      else await agentService.createProjectAgent(workspaceSlug, projectId, payload);
      setToast({ type: "success", title: "Saved", message: "Project position updated." });
      onClose();
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not save the position",
        message: getAgentErrorMessage(error, "Please try again."),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="md" aria-label={assignment ? "Edit position" : "Assign agent"}>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            <h2 className="text-h5-medium text-secondary">{assignment ? "Edit position" : "Assign agent"}</h2>
            <SelectField<TAgentRole>
              size="lg"
              aria-label="Position"
              label="Position"
              value={role}
              onValueChange={(value) => value && setRole(value)}
              options={AGENT_ROLES}
            />
            <SelectField<string>
              size="lg"
              aria-label="Agent"
              label="Agent"
              value={agentId}
              onValueChange={setAgentId}
              options={agentOptions}
              placeholder={agents.length ? "Select an agent" : "No agents in this workspace yet"}
              disabled={!agents.length}
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="model_override"
              label="Model override"
              value={modelOverride}
              onChange={(e) => setModelOverride(e.target.value)}
              placeholder="Optional — overrides the agent model for this project"
              autoComplete="off"
            />
            <div className="flex items-center justify-between">
              <span className="text-body-sm-medium text-secondary">Active</span>
              <Switch size="sm" checked={isActive} onCheckedChange={setIsActive} aria-label="Active" />
            </div>
          </div>
          <div className="flex items-center justify-end gap-3 border-t-[0.5px] border-subtle px-5 py-4">
            <Button variant="secondary" size="md" stretch="auto" label="Cancel" onClick={onClose} />
            <Button
              type="submit"
              variant="primary"
              size="md"
              stretch="auto"
              label={isSubmitting ? "Saving..." : "Save"}
              loading={isSubmitting}
            />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
