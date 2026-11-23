/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
// plane imports
import { AGENT_EFFORT_LEVELS, AGENT_PROVIDERS } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { Dialog, DialogContent } from "@makeplane/propel/components/dialog";
import { InputField } from "@makeplane/propel/components/input-field";
import { SelectField } from "@makeplane/propel/components/select-field";
import { Switch } from "@makeplane/propel/components/switch";
import { TextAreaField } from "@makeplane/propel/components/text-area-field";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TAgent, TAgentPayload, TAgentProvider } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

type Props = {
  workspaceSlug: string;
  /** `null` creates a new agent, an agent edits it, `undefined` keeps the dialog closed. */
  agent?: TAgent | null;
  isOpen: boolean;
  onClose: () => void;
};

const EMPTY_FORM: TAgentPayload = {
  name: "",
  provider: "anthropic",
  model: "",
  api_key: "",
  base_url: "",
  system_prompt: "",
  reasoning_effort: "",
  is_active: true,
};

export function AgentFormDialog({ workspaceSlug, agent, isOpen, onClose }: Props) {
  // translation
  const { t } = useTranslation();
  // states
  const [form, setForm] = useState<TAgentPayload>(EMPTY_FORM);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    if (agent) {
      setForm({
        name: agent.name,
        provider: agent.provider,
        model: agent.model,
        api_key: "",
        base_url: agent.base_url ?? "",
        system_prompt: agent.system_prompt ?? "",
        reasoning_effort: agent.reasoning_effort ?? "",
        is_active: agent.is_active,
      });
    } else {
      setForm(EMPTY_FORM);
    }
  }, [isOpen, agent]);

  const setField = <K extends keyof TAgentPayload>(key: K, value: TAgentPayload[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleProviderChange = (provider: TAgentProvider | null) => {
    if (!provider) return;
    const definition = AGENT_PROVIDERS.find((item) => item.value === provider);
    setForm((current) => ({ ...current, provider, base_url: definition?.defaultBaseUrl ?? "" }));
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.name.trim() || !form.model.trim()) {
      setToast({
        type: "error",
        title: t("workspace_settings.settings.ai_agents.form.missing_fields_title"),
        message: t("workspace_settings.settings.ai_agents.form.missing_fields_message"),
      });
      return;
    }
    if ((form.provider === "opencode" || form.provider === "custom") && !form.base_url?.trim()) {
      setToast({
        type: "error",
        title: t("workspace_settings.settings.ai_agents.form.missing_base_url_title"),
        message: t("workspace_settings.settings.ai_agents.form.missing_base_url_message"),
      });
      return;
    }
    if (!agent && !form.api_key?.trim()) {
      setToast({
        type: "error",
        title: t("workspace_settings.settings.ai_agents.form.missing_api_key_title"),
        message: t("workspace_settings.settings.ai_agents.form.missing_api_key_message"),
      });
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = { ...form };
      if (!payload.api_key) delete payload.api_key;
      if (agent) await agentService.updateAgent(workspaceSlug, agent.id, payload);
      else await agentService.createAgent(workspaceSlug, payload);
      setToast({
        type: "success",
        title: t("workspace_settings.settings.ai_agents.form.saved_title"),
        message: agent
          ? t("workspace_settings.settings.ai_agents.form.updated_message")
          : t("workspace_settings.settings.ai_agents.form.created_message"),
      });
      onClose();
    } catch (error) {
      setToast({
        type: "error",
        title: t("workspace_settings.settings.ai_agents.form.save_failed_title"),
        message: getAgentErrorMessage(error, t("agent_run.toast.try_again")),
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
      <DialogContent
        size="md"
        aria-label={
          agent
            ? t("workspace_settings.settings.ai_agents.form.edit_title")
            : t("workspace_settings.settings.ai_agents.form.add_title")
        }
      >
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            <h2 className="text-h5-medium text-secondary">
              {agent
                ? t("workspace_settings.settings.ai_agents.form.edit_title")
                : t("workspace_settings.settings.ai_agents.form.add_title")}
            </h2>
            <InputField
              size="lg"
              orientation="vertical"
              name="name"
              label={t("workspace_settings.settings.ai_agents.form.name")}
              required
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="Opus 5.5"
              autoComplete="off"
            />
            <SelectField<TAgentProvider>
              size="lg"
              aria-label={t("workspace_settings.settings.ai_agents.form.provider")}
              label={t("workspace_settings.settings.ai_agents.form.provider")}
              value={form.provider}
              onValueChange={handleProviderChange}
              options={AGENT_PROVIDERS.map(({ value, label }) => ({ value, label }))}
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="model"
              label={t("workspace_settings.settings.ai_agents.form.model")}
              required
              value={form.model}
              onChange={(e) => setField("model", e.target.value)}
              placeholder={form.provider === "anthropic" ? "claude-opus-5.5" : "deepseek-4.1-flash"}
              autoComplete="off"
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="api_key"
              label={t("workspace_settings.settings.ai_agents.form.api_key")}
              type="password"
              required={!agent}
              value={form.api_key}
              onChange={(e) => setField("api_key", e.target.value)}
              placeholder={
                agent?.has_api_key
                  ? t("workspace_settings.settings.ai_agents.form.api_key_stored")
                  : t("workspace_settings.settings.ai_agents.form.api_key_placeholder")
              }
              autoComplete="new-password"
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="base_url"
              label={t("workspace_settings.settings.ai_agents.form.base_url")}
              value={form.base_url}
              onChange={(e) => setField("base_url", e.target.value)}
              placeholder="https://api.anthropic.com"
              autoComplete="off"
            />
            <SelectField<string>
              size="lg"
              aria-label={t("workspace_settings.settings.ai_agents.form.reasoning_effort")}
              label={t("workspace_settings.settings.ai_agents.form.reasoning_effort")}
              description={t("workspace_settings.settings.ai_agents.form.reasoning_effort_description")}
              value={form.reasoning_effort ?? ""}
              onValueChange={(value) =>
                setField("reasoning_effort", (value ?? "") as TAgentPayload["reasoning_effort"])
              }
              options={AGENT_EFFORT_LEVELS}
            />
            <TextAreaField
              size="lg"
              name="system_prompt"
              label={t("workspace_settings.settings.ai_agents.form.system_prompt")}
              value={form.system_prompt}
              onChange={(e) => setField("system_prompt", e.target.value)}
              placeholder={t("workspace_settings.settings.ai_agents.form.system_prompt_placeholder")}
            />
            <div className="flex items-center justify-between">
              <span className="text-body-sm-medium text-secondary">
                {t("workspace_settings.settings.ai_agents.form.active")}
              </span>
              <Switch
                size="sm"
                checked={Boolean(form.is_active)}
                onCheckedChange={(checked) => setField("is_active", checked)}
                aria-label={t("workspace_settings.settings.ai_agents.form.active")}
              />
            </div>
          </div>
          <div className="flex items-center justify-end gap-3 border-t-[0.5px] border-subtle px-5 py-4">
            <Button variant="secondary" size="md" stretch="auto" label={t("common.cancel")} onClick={onClose} />
            <Button
              type="submit"
              variant="primary"
              size="md"
              stretch="auto"
              label={isSubmitting ? t("workspace_settings.settings.ai_agents.form.saving") : t("common.save")}
              loading={isSubmitting}
            />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
