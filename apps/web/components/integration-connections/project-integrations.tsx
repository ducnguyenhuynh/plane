/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import useSWR from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { InputField } from "@makeplane/propel/components/input-field";
import { SelectField } from "@makeplane/propel/components/select-field";
import { Switch } from "@makeplane/propel/components/switch";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TProjectIntegrationPayload, TSlackEvents } from "@plane/types";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// services
import { IntegrationConnectionService } from "@/services/integrations";
// local imports
import { backstageEntityUrl, getIntegrationErrorMessage } from "./utils";

const integrationService = new IntegrationConnectionService();

const DEFAULT_EVENTS: TSlackEvents = {
  issue_created: true,
  issue_updated: false,
  comment_created: true,
  agent_run: true,
};

type Props = {
  workspaceSlug: string;
  projectId: string;
  canManage: boolean;
};

export function ProjectIntegrations({ workspaceSlug, projectId, canManage }: Props) {
  // translation
  const { t } = useTranslation();
  // store hooks
  const { allowPermissions } = useUserPermissions();
  // derived values
  // The repositories endpoint is workspace-admin only, so members must not call it.
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const { data: integration, mutate } = useSWR(`PROJECT_INTEGRATION_${projectId}`, () =>
    integrationService.fetchProjectIntegration(workspaceSlug, projectId)
  );
  const { data: github } = useSWR(`GITHUB_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchGitHubConnection(workspaceSlug)
  );
  const { data: backstage } = useSWR(`BACKSTAGE_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchBackstageConnection(workspaceSlug)
  );
  const { data: repositories } = useSWR(
    isWorkspaceAdmin && github?.has_token ? `GITHUB_REPOS_${workspaceSlug}` : null,
    () => integrationService.fetchGitHubRepositories(workspaceSlug)
  );

  const [repository, setRepository] = useState("");
  const [branch, setBranch] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [slackEvents, setSlackEvents] = useState<TSlackEvents>(DEFAULT_EVENTS);
  const [entityRef, setEntityRef] = useState("");
  const [isBackstageEnabled, setIsBackstageEnabled] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!integration) return;
    setRepository(integration.github_repository ?? "");
    setBranch(integration.github_default_branch ?? "");
    // A stored row may predate `agent_run`; the backend treats a missing value as on, so mirror that.
    setSlackEvents({ ...DEFAULT_EVENTS, ...integration.slack_events });
    setEntityRef(integration.backstage_entity_ref ?? "");
    setIsBackstageEnabled(integration.is_backstage_enabled ?? false);
  }, [integration]);

  const save = async (data: TProjectIntegrationPayload, section: string, message: string) => {
    setBusy(section);
    try {
      await integrationService.updateProjectIntegration(workspaceSlug, projectId, data);
      await mutate();
      setToast({ type: "success", title: t("integrations.project.saved_title"), message });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.project.save_failed_title"),
        message: getIntegrationErrorMessage(error, t("agent_run.toast.try_again")),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleTestSlack = async () => {
    setBusy("slack-test");
    try {
      const result = await integrationService.testSlack(workspaceSlug, projectId);
      setToast({
        type: result.ok ? "success" : "error",
        title: result.ok ? t("integrations.project.slack_test_sent") : t("integrations.project.slack_test_failed"),
        message: result.message,
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.project.slack_test_failed"),
        message: getIntegrationErrorMessage(error, t("integrations.project.slack_test_failed_message")),
      });
    } finally {
      setBusy(null);
    }
  };

  const backstageUrl = backstageEntityUrl(backstage?.base_url ?? "", entityRef);

  return (
    <div className="flex flex-col gap-4">
      {/* GitHub */}
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">{t("integrations.project.github_title")}</h4>
          <p className="text-body-xs-regular text-tertiary">{t("integrations.project.github_description")}</p>
        </div>
        {!github?.has_token ? (
          <p className="text-body-xs-regular text-tertiary">{t("integrations.project.github_not_connected")}</p>
        ) : !isWorkspaceAdmin ? (
          <div className="flex flex-col gap-1">
            <span className="text-body-sm-regular text-secondary">{integration?.github_repository || "—"}</span>
            {integration?.github_default_branch && (
              <span className="text-body-xs-regular text-tertiary">{integration.github_default_branch}</span>
            )}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            <SelectField<string>
              size="lg"
              aria-label={t("integrations.project.repository")}
              label={t("integrations.project.repository")}
              value={repository}
              onValueChange={(value) => {
                if (!value) return;
                setRepository(value);
                const selected = repositories?.find((repo) => repo.full_name === value);
                if (selected) setBranch(selected.default_branch);
              }}
              options={(repositories ?? []).map((repo) => ({
                value: repo.full_name,
                label: repo.private ? `${repo.full_name} ${t("integrations.project.private_repo")}` : repo.full_name,
              }))}
              placeholder={
                repositories
                  ? t("integrations.project.repository_placeholder")
                  : t("integrations.project.repository_loading")
              }
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="github_branch"
              label={t("integrations.project.default_branch")}
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              placeholder="main"
              autoComplete="off"
            />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {canManage && github?.has_token && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={busy === "github" ? t("integrations.project.saving") : t("integrations.project.save")}
              loading={busy === "github"}
              disabled={!repository}
              onClick={() =>
                void save(
                  {
                    github_repository: repository,
                    github_default_branch: branch,
                    is_github_enabled: true,
                  },
                  "github",
                  t("integrations.project.repository_linked")
                )
              }
            />
          )}
          {canManage && integration?.is_github_enabled && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={t("integrations.project.unlink")}
              onClick={() =>
                void save(
                  { is_github_enabled: false, github_default_branch: "" },
                  "github",
                  t("integrations.project.repository_unlinked")
                )
              }
              disabled={busy === "github"}
            />
          )}
          {integration?.is_github_enabled && integration.github_repository_url && (
            <a
              className="text-body-xs-medium text-link-primary hover:underline"
              href={integration.github_repository_url}
              target="_blank"
              rel="noreferrer"
            >
              {integration.github_repository}
            </a>
          )}
        </div>
      </div>

      {/* Slack */}
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">{t("integrations.project.slack_title")}</h4>
          <p className="text-body-xs-regular text-tertiary">{t("integrations.project.slack_description")}</p>
        </div>
        <InputField
          size="lg"
          orientation="vertical"
          name="slack_webhook"
          label={t("integrations.project.webhook_url")}
          type="password"
          value={webhookUrl}
          onChange={(event) => setWebhookUrl(event.target.value)}
          placeholder={
            integration?.has_slack_webhook
              ? t("integrations.project.webhook_stored")
              : t("integrations.project.webhook_placeholder")
          }
          autoComplete="new-password"
          disabled={!canManage}
        />
        <div className="flex flex-col gap-2">
          {[
            { key: "issue_created" as const, label: t("integrations.project.event_issue_created") },
            { key: "issue_updated" as const, label: t("integrations.project.event_issue_updated") },
            { key: "comment_created" as const, label: t("integrations.project.event_comment_created") },
            { key: "agent_run" as const, label: t("integrations.project.event_agent_run") },
          ].map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between gap-4">
              <span className="text-body-sm-regular text-secondary">{label}</span>
              <Switch
                size="sm"
                checked={Boolean(slackEvents[key])}
                onCheckedChange={(checked) => setSlackEvents((current) => ({ ...current, [key]: checked }))}
                aria-label={label}
                disabled={!canManage}
              />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canManage && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={busy === "slack" ? t("integrations.project.saving") : t("integrations.project.save")}
              loading={busy === "slack"}
              onClick={() => {
                if (!webhookUrl.trim() && !integration?.has_slack_webhook) {
                  setToast({
                    type: "error",
                    title: t("integrations.project.webhook_required_title"),
                    message: t("integrations.project.webhook_required_message"),
                  });
                  return;
                }
                void save(
                  {
                    ...(webhookUrl.trim() ? { slack_webhook_url: webhookUrl.trim() } : {}),
                    slack_events: slackEvents,
                    is_slack_enabled: true,
                  },
                  "slack",
                  t("integrations.project.slack_saved")
                );
                setWebhookUrl("");
              }}
            />
          )}
          {canManage && integration?.has_slack_webhook && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={busy === "slack-test" ? t("integrations.project.testing") : t("integrations.project.send_test")}
              loading={busy === "slack-test"}
              onClick={() => void handleTestSlack()}
            />
          )}
          {canManage && integration?.is_slack_enabled && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={t("integrations.project.disable")}
              disabled={busy === "slack"}
              onClick={() => void save({ is_slack_enabled: false }, "slack", t("integrations.project.slack_disabled"))}
            />
          )}
        </div>
      </div>

      {/* Backstage */}
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">{t("integrations.project.backstage_title")}</h4>
          <p className="text-body-xs-regular text-tertiary">{t("integrations.project.backstage_description")}</p>
        </div>
        {!backstage?.base_url ? (
          <p className="text-body-xs-regular text-tertiary">{t("integrations.project.backstage_not_configured")}</p>
        ) : (
          <InputField
            size="lg"
            orientation="vertical"
            name="backstage_entity"
            label={t("integrations.project.entity_ref")}
            value={entityRef}
            onChange={(event) => setEntityRef(event.target.value)}
            placeholder={t("integrations.project.entity_placeholder")}
            autoComplete="off"
            disabled={!canManage}
          />
        )}
        <div className="flex flex-wrap items-center gap-2">
          {canManage && backstage?.base_url && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={busy === "backstage" ? t("integrations.project.saving") : t("integrations.project.save")}
              loading={busy === "backstage"}
              disabled={!entityRef.trim()}
              onClick={() =>
                void save(
                  { backstage_entity_ref: entityRef.trim(), is_backstage_enabled: true },
                  "backstage",
                  t("integrations.project.backstage_linked")
                )
              }
            />
          )}
          {backstageUrl && isBackstageEnabled && (
            <a
              className="text-body-xs-medium text-link-primary hover:underline"
              href={backstageUrl}
              target="_blank"
              rel="noreferrer"
            >
              {t("integrations.project.open_in_backstage")}
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
