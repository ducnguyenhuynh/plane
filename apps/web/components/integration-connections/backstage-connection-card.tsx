/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
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

export function BackstageConnectionCard({ workspaceSlug }: { workspaceSlug: string }) {
  // translation
  const { t } = useTranslation();
  // states
  const { data, mutate, isLoading } = useSWR(`BACKSTAGE_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchBackstageConnection(workspaceSlug)
  );
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (data) setBaseUrl(data.base_url ?? "");
  }, [data]);

  const handleSave = async () => {
    if (!baseUrl.trim()) {
      setToast({
        type: "error",
        title: t("integrations.workspace.url_required_title"),
        message: t("integrations.workspace.url_required_message"),
      });
      return;
    }
    setIsSaving(true);
    try {
      await integrationService.connectBackstage(workspaceSlug, {
        base_url: baseUrl.trim(),
        ...(token.trim() ? { token: token.trim() } : {}),
      });
      setToken("");
      void mutate();
      setToast({
        type: "success",
        title: t("integrations.workspace.backstage_saved_title"),
        message: t("integrations.workspace.backstage_saved_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.workspace.backstage_save_failed_title"),
        message: getIntegrationErrorMessage(error, t("integrations.workspace.backstage_save_failed_message")),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleTest = async () => {
    setIsTesting(true);
    try {
      const result = await integrationService.testBackstage(workspaceSlug);
      setToast({
        type: result.ok ? "success" : "error",
        title: result.ok ? t("integrations.workspace.connection_works") : t("integrations.workspace.connection_failed"),
        message: result.message,
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.workspace.connection_failed"),
        message: getIntegrationErrorMessage(error, t("integrations.workspace.backstage_unreachable")),
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleDisconnect = async () => {
    setIsSaving(true);
    try {
      await integrationService.disconnectBackstage(workspaceSlug);
      setBaseUrl("");
      void mutate();
      setToast({
        type: "success",
        title: t("integrations.workspace.disconnected_title"),
        message: t("integrations.workspace.backstage_disconnected_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("integrations.workspace.backstage_disconnect_failed_title"),
        message: getIntegrationErrorMessage(error, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
      <div className="flex flex-col gap-1">
        <h4 className="text-body-sm-medium text-primary">{t("integrations.workspace.backstage_title")}</h4>
        <p className="text-body-xs-regular text-tertiary">{t("integrations.workspace.backstage_description")}</p>
      </div>

      {isLoading && <p className="text-body-xs-regular text-tertiary">{t("common.loading")}</p>}

      <div className="grid gap-3 md:grid-cols-2">
        <InputField
          size="lg"
          orientation="vertical"
          name="backstage_url"
          label={t("integrations.workspace.backstage_url")}
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder="https://backstage.example.com"
          autoComplete="off"
        />
        <InputField
          size="lg"
          orientation="vertical"
          name="backstage_token"
          label={t("integrations.workspace.backstage_token")}
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder={
            data?.has_token
              ? t("integrations.workspace.backstage_token_stored")
              : t("integrations.workspace.backstage_token_placeholder")
          }
          autoComplete="new-password"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="md"
          stretch="auto"
          label={isSaving ? t("integrations.project.saving") : t("integrations.project.save")}
          loading={isSaving}
          onClick={() => void handleSave()}
        />
        {data?.has_token !== undefined && (
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            label={isTesting ? t("integrations.project.testing") : t("integrations.workspace.test_connection")}
            loading={isTesting}
            onClick={() => void handleTest()}
            disabled={!baseUrl.trim()}
          />
        )}
        {data?.base_url && (
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            label={t("integrations.workspace.disconnect")}
            onClick={() => void handleDisconnect()}
            disabled={isSaving}
          />
        )}
      </div>
    </div>
  );
}
