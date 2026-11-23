/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { InputField } from "@makeplane/propel/components/input-field";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
// services
import { IntegrationConnectionService } from "@/services/integrations";
// local imports
import { getIntegrationErrorMessage } from "./utils";

const integrationService = new IntegrationConnectionService();

export function GitHubConnectionCard({ workspaceSlug }: { workspaceSlug: string }) {
  // translation
  const { t } = useTranslation();
  // states
  const { data, mutate, isLoading } = useSWR(`GITHUB_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchGitHubConnection(workspaceSlug)
  );
  const [token, setToken] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const handleConnect = async () => {
    if (!token.trim()) {
      setToast({
        type: "error",
        title: t("integrations.workspace.token_required_title"),
        message: t("integrations.workspace.token_required_message"),
      });
      return;
    }
    setIsSaving(true);
    try {
      await integrationService.connectGitHub(workspaceSlug, token.trim());
      setToken("");
      void mutate();
      setToast({
        type: "success",
        title: t("integrations.workspace.github_connected_title"),
        message: t("integrations.workspace.github_connected_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.workspace.github_connect_failed_title"),
        message: getIntegrationErrorMessage(error, t("integrations.workspace.github_connect_failed_message")),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDisconnect = async () => {
    setIsSaving(true);
    try {
      await integrationService.disconnectGitHub(workspaceSlug);
      void mutate();
      setToast({
        type: "success",
        title: t("integrations.workspace.disconnected_title"),
        message: t("integrations.workspace.github_disconnected_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.workspace.github_disconnect_failed_title"),
        message: getIntegrationErrorMessage(error, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
      <div className="flex flex-col gap-1">
        <h4 className="text-body-sm-medium text-primary">{t("integrations.workspace.github_title")}</h4>
        <p className="text-body-xs-regular text-tertiary">{t("integrations.workspace.github_description")}</p>
      </div>

      {isLoading && <p className="text-body-xs-regular text-tertiary">{t("common.loading")}</p>}

      {data?.has_token ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-body-sm-regular text-secondary">
            {t("integrations.workspace.connected_as", { login: data.login })}
          </span>
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label={isSaving ? t("integrations.workspace.disconnecting") : t("integrations.workspace.disconnect")}
            loading={isSaving}
            onClick={() => void handleDisconnect()}
          />
        </div>
      ) : (
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <div className="grow">
            <InputField
              size="lg"
              orientation="vertical"
              name="github_token"
              label={t("integrations.workspace.token")}
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder={t("integrations.workspace.token_placeholder")}
              autoComplete="new-password"
            />
          </div>
          <Button
            variant="primary"
            size="md"
            stretch="auto"
            label={isSaving ? t("integrations.workspace.connecting") : t("integrations.workspace.connect")}
            loading={isSaving}
            onClick={() => void handleConnect()}
          />
        </div>
      )}
    </div>
  );
}
