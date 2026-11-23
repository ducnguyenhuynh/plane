/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import useSWR from "swr";
// plane imports
import { AGENT_ROLES } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { InputField } from "@makeplane/propel/components/input-field";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TAgentRole, TProjectAgent } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { AssignAgentDialog } from "./assign-agent-dialog";
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

const TEAM_SIZE_MIN = 1;
const TEAM_SIZE_MAX = 10;

/** Which team-size count each run role is limited by. `reviewer` is not used by runs. */
const ROLE_COUNT_KEYS: Partial<Record<TAgentRole, "pm_count" | "developer_count" | "qa_count">> = {
  project_manager: "pm_count",
  developer: "developer_count",
  qa: "qa_count",
};

type Props = {
  workspaceSlug: string;
  projectId: string;
  canManage: boolean;
};

export function ProjectAgentsSettings({ workspaceSlug, projectId, canManage }: Props) {
  // translation
  const { t } = useTranslation();
  const {
    data: assignments,
    mutate,
    error,
  } = useSWR(`PROJECT_AI_AGENTS_${projectId}`, () => agentService.fetchProjectAgents(workspaceSlug, projectId));
  const { data: agents } = useSWR(`AI_AGENTS_${workspaceSlug}`, () => agentService.fetchAgents(workspaceSlug));
  const { data: team, mutate: mutateTeam } = useSWR(`PROJECT_AGENT_TEAM_${projectId}`, () =>
    agentService.fetchAgentTeam(workspaceSlug, projectId)
  );
  const [formAssignment, setFormAssignment] = useState<TProjectAgent | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<TProjectAgent | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [teamCounts, setTeamCounts] = useState({ pm_count: "1", qa_count: "1", developer_count: "3" });
  const [isSavingTeam, setIsSavingTeam] = useState(false);

  useEffect(() => {
    if (!team) return;
    setTeamCounts({
      pm_count: String(team.pm_count),
      qa_count: String(team.qa_count),
      developer_count: String(team.developer_count),
    });
  }, [team]);

  const saveTeam = async () => {
    const counts = {
      pm_count: Number(teamCounts.pm_count),
      qa_count: Number(teamCounts.qa_count),
      developer_count: Number(teamCounts.developer_count),
    };
    if (
      Object.values(counts).some((value) => !Number.isInteger(value) || value < TEAM_SIZE_MIN || value > TEAM_SIZE_MAX)
    ) {
      setToast({
        type: "error",
        title: t("project_settings.agents.team_size.invalid_title"),
        message: t("project_settings.agents.team_size.invalid_message", { min: TEAM_SIZE_MIN, max: TEAM_SIZE_MAX }),
      });
      return;
    }
    setIsSavingTeam(true);
    try {
      await agentService.updateAgentTeam(workspaceSlug, projectId, counts);
      await mutateTeam();
      setToast({
        type: "success",
        title: t("project_settings.agents.team_size.saved_title"),
        message: t("project_settings.agents.team_size.saved_message"),
      });
    } catch (requestError) {
      setToast({
        type: "error",
        title: t("project_settings.agents.team_size.save_failed_title"),
        message: getAgentErrorMessage(requestError, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsSavingTeam(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await agentService.deleteProjectAgent(workspaceSlug, projectId, deleteTarget.id);
      setDeleteTarget(null);
      void mutate();
      setToast({
        type: "success",
        title: t("project_settings.agents.positions.removed_title"),
        message: t("project_settings.agents.positions.removed_message"),
      });
    } catch (requestError) {
      setToast({
        type: "error",
        title: t("project_settings.agents.positions.remove_failed_title"),
        message: getAgentErrorMessage(requestError, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  const roleLabel = (role: TAgentRole) => t(`project_settings.agents.roles.${role}`);
  const reviewerAssignments = assignments?.filter((assignment) => assignment.role === "reviewer") ?? [];

  const renderAssignment = (assignment: TProjectAgent) => (
    <div
      key={assignment.id}
      className="flex flex-col gap-3 rounded-md border border-subtle p-4 md:flex-row md:items-center md:justify-between"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body-sm-medium text-primary">{assignment.agent.name}</span>
          <span className="text-body-xs-regular text-tertiary">
            {assignment.model_override || assignment.agent.model}
            {assignment.model_override ? ` ${t("project_settings.agents.positions.model_override")}` : ""}
          </span>
          {!assignment.is_active && (
            <span className="text-body-xs-regular text-danger-primary">
              {t("project_settings.agents.positions.agent_inactive")}
            </span>
          )}
          {!assignment.in_team && (
            <span className="text-body-xs-regular text-warning-primary">
              {t("project_settings.agents.positions.bench")}
            </span>
          )}
        </div>
      </div>
      {canManage && (
        <div className="flex shrink-0 items-center gap-2">
          {/* Reviewer rows predate the role being retired; the API rejects saving them, so only removal is offered. */}
          {assignment.role !== "reviewer" && (
            <Button
              variant="secondary"
              size="sm"
              stretch="auto"
              label={t("common.edit")}
              onClick={() => setFormAssignment(assignment)}
            />
          )}
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label={t("common.remove")}
            onClick={() => setDeleteTarget(assignment)}
          />
        </div>
      )}
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">{t("project_settings.agents.team_size.title")}</h4>
          <p className="text-body-xs-regular text-tertiary">{t("project_settings.agents.team_size.description")}</p>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <InputField
            size="lg"
            orientation="vertical"
            name="pm_count"
            label={t("project_settings.agents.team_size.pm_label")}
            description={t("project_settings.agents.team_size.pm_help")}
            type="number"
            min={TEAM_SIZE_MIN}
            max={TEAM_SIZE_MAX}
            step={1}
            value={teamCounts.pm_count}
            onChange={(event) => setTeamCounts((current) => ({ ...current, pm_count: event.target.value }))}
            disabled={!canManage}
          />
          <InputField
            size="lg"
            orientation="vertical"
            name="developer_count"
            label={t("project_settings.agents.team_size.developer_label")}
            description={t("project_settings.agents.team_size.developer_help")}
            type="number"
            min={TEAM_SIZE_MIN}
            max={TEAM_SIZE_MAX}
            step={1}
            value={teamCounts.developer_count}
            onChange={(event) => setTeamCounts((current) => ({ ...current, developer_count: event.target.value }))}
            disabled={!canManage}
          />
          <InputField
            size="lg"
            orientation="vertical"
            name="qa_count"
            label={t("project_settings.agents.team_size.qa_label")}
            description={t("project_settings.agents.team_size.qa_help")}
            type="number"
            min={TEAM_SIZE_MIN}
            max={TEAM_SIZE_MAX}
            step={1}
            value={teamCounts.qa_count}
            onChange={(event) => setTeamCounts((current) => ({ ...current, qa_count: event.target.value }))}
            disabled={!canManage}
          />
        </div>
        {canManage && (
          <div>
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={
                isSavingTeam
                  ? t("project_settings.agents.team_size.saving")
                  : t("project_settings.agents.team_size.save")
              }
              loading={isSavingTeam}
              disabled={!team}
              onClick={() => void saveTeam()}
            />
          </div>
        )}
      </div>

      {canManage && (
        <div className="flex items-center justify-end">
          <Button
            variant="primary"
            size="md"
            stretch="auto"
            label={t("project_settings.agents.positions.assign")}
            onClick={() => setFormAssignment(null)}
          />
        </div>
      )}

      {!assignments && !error && (
        <p className="text-body-sm-regular text-tertiary">{t("project_settings.agents.positions.loading")}</p>
      )}
      {error && (
        <p className="text-body-sm-regular text-danger-primary">{t("project_settings.agents.positions.load_failed")}</p>
      )}
      {assignments && assignments.length === 0 && (
        <p className="text-body-sm-regular text-tertiary">{t("project_settings.agents.positions.empty")}</p>
      )}

      {AGENT_ROLES.map(({ value: role }) => {
        const roleAssignments = assignments?.filter((assignment) => assignment.role === role) ?? [];
        if (!roleAssignments.length) return null;
        const countKey = ROLE_COUNT_KEYS[role];
        const count = countKey ? team?.[countKey] : undefined;
        const activeCount = roleAssignments.filter((assignment) => assignment.is_active && assignment.in_team).length;
        return (
          <section key={role} className="flex flex-col gap-2">
            <h4 className="text-body-sm-medium text-secondary">{roleLabel(role)}</h4>
            {count !== undefined && activeCount < count && (
              <p className="text-body-xs-regular text-warning-primary">
                {t("project_settings.agents.positions.seat_warning", {
                  filled: activeCount,
                  count,
                  role: roleLabel(role).toLowerCase(),
                })}
              </p>
            )}
            {roleAssignments.map(renderAssignment)}
          </section>
        );
      })}

      {reviewerAssignments.length > 0 && (
        <section className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-secondary">
            {t("project_settings.agents.positions.reviewer_heading")}
          </h4>
          {reviewerAssignments.map(renderAssignment)}
        </section>
      )}

      {canManage && (
        <AssignAgentDialog
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          agents={agents ?? []}
          assignment={formAssignment}
          isOpen={formAssignment !== undefined}
          onClose={() => {
            setFormAssignment(undefined);
            void mutate();
          }}
        />
      )}

      <ConfirmDialog
        isOpen={Boolean(deleteTarget)}
        isSubmitting={isDeleting}
        title={t("project_settings.agents.positions.delete_title")}
        content={t("project_settings.agents.positions.delete_content", {
          name: deleteTarget?.agent.name ?? "",
          role: deleteTarget ? roleLabel(deleteTarget.role) : "",
        })}
        handleClose={() => setDeleteTarget(null)}
        handleSubmit={handleDelete}
      />
    </div>
  );
}
