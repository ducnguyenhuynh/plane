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
import { Switch } from "@makeplane/propel/components/switch";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TProjectAgentMcpServer, TProjectAgentSkill } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

/** Header rows get a client-only id; `crypto.randomUUID` is missing on plain-HTTP self-hosted instances. */
let nextHeaderId = 0;

const TEXTAREA_CLASS =
  "w-full rounded-md border border-subtle bg-layer-1 p-2 text-body-sm-regular text-primary placeholder:text-tertiary focus-visible:ring-2 focus-visible:ring-accent-strong focus-visible:outline-none";

type Props = {
  workspaceSlug: string;
  projectId: string;
  canManage: boolean;
};

type TeamPayload = Parameters<AgentService["updateAgentTeam"]>[2];
type Header = { id: string; name: string; value: string; saved: boolean };
type Server = { name: string; url: string; enabled: boolean; headers: Header[] };
type SkillDraft = { index: number | null } & TProjectAgentSkill;
type ServerDraft = { index: number | null } & Server;

export function ProjectAgentSandboxSettings({ workspaceSlug, projectId, canManage }: Props) {
  // translation
  const { t } = useTranslation();
  const { data: team, mutate: mutateTeam } = useSWR(`PROJECT_AGENT_TEAM_${projectId}`, () =>
    agentService.fetchAgentTeam(workspaceSlug, projectId)
  );
  const [hosts, setHosts] = useState("");
  const [websearch, setWebsearch] = useState(false);
  const [webfetch, setWebfetch] = useState(false);
  const [skills, setSkills] = useState<TProjectAgentSkill[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [skillDraft, setSkillDraft] = useState<SkillDraft | null>(null);
  const [serverDraft, setServerDraft] = useState<ServerDraft | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // One effect per section, keyed on that section only, so saving one section keeps unsaved edits elsewhere.
  const hostsSource = JSON.stringify(team?.allowed_hosts ?? []);
  const webSource = JSON.stringify([team?.websearch_enabled ?? false, team?.webfetch_enabled ?? false]);
  const skillsSource = JSON.stringify(team?.skills ?? []);
  const mcpSource = JSON.stringify(team?.mcp_servers ?? []);

  useEffect(() => {
    setHosts((JSON.parse(hostsSource) as string[]).join("\n"));
  }, [hostsSource]);

  useEffect(() => {
    const [search, fetch] = JSON.parse(webSource) as [boolean, boolean];
    setWebsearch(search);
    setWebfetch(fetch);
  }, [webSource]);

  useEffect(() => {
    setSkills(JSON.parse(skillsSource) as TProjectAgentSkill[]);
  }, [skillsSource]);

  useEffect(() => {
    setServers(
      (JSON.parse(mcpSource) as TProjectAgentMcpServer[]).map((server) => ({
        name: server.name,
        url: server.url,
        enabled: server.enabled,
        // "Saved" headers are sent back empty so the server keeps their stored secret.
        headers: server.header_names.map((name) => ({ id: name, name, value: "", saved: true })),
      }))
    );
  }, [mcpSource]);

  const save = async (section: string, payload: TeamPayload) => {
    setBusy(section);
    try {
      await agentService.updateAgentTeam(workspaceSlug, projectId, payload);
      await mutateTeam();
      setToast({
        type: "success",
        title: t("project_settings.agents.sandbox.saved_title"),
        message: t("project_settings.agents.sandbox.saved_message"),
      });
    } catch (requestError) {
      setToast({
        type: "error",
        title: t("project_settings.agents.sandbox.save_failed"),
        message: getAgentErrorMessage(requestError, t("agent_run.toast.try_again")),
      });
    } finally {
      setBusy(null);
    }
  };

  const renderSave = (section: string, payload: () => TeamPayload) => (
    <div>
      <Button
        variant="primary"
        size="md"
        stretch="auto"
        label={t("project_settings.agents.sandbox.save")}
        loading={busy === section}
        disabled={!canManage || !team || busy !== null}
        onClick={() => void save(section, payload())}
      />
    </div>
  );

  const commitSkill = () => {
    if (!skillDraft) return;
    const { index, ...draft } = skillDraft;
    const skill = { ...draft, name: draft.name.trim() };
    setSkills((current) =>
      index === null ? [...current, skill] : current.map((item, itemIndex) => (itemIndex === index ? skill : item))
    );
    setSkillDraft(null);
  };

  const commitServer = () => {
    if (!serverDraft) return;
    const { index, ...draft } = serverDraft;
    const server: Server = {
      ...draft,
      name: draft.name.trim(),
      headers: draft.headers
        .filter((header) => header.name.trim())
        .map((header) => ({ id: header.id, name: header.name.trim(), value: header.value, saved: header.saved })),
    };
    setServers((current) =>
      index === null ? [...current, server] : current.map((item, itemIndex) => (itemIndex === index ? server : item))
    );
    setServerDraft(null);
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-secondary">{t("project_settings.agents.sandbox.network.title")}</h4>
        <p className="text-body-xs-regular text-tertiary">{t("project_settings.agents.sandbox.network.hint")}</p>
        <div className="flex flex-col gap-1">
          <span className="text-body-xs-regular text-secondary">
            {t("project_settings.agents.sandbox.network.defaults")}
          </span>
          <span className="text-body-sm-regular text-primary">{team?.default_allowed_hosts.join(", ")}</span>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="agent-sandbox-hosts" className="text-body-xs-regular text-secondary">
            {t("project_settings.agents.sandbox.network.extra")}
          </label>
          <textarea
            id="agent-sandbox-hosts"
            rows={4}
            value={hosts}
            onChange={(event) => setHosts(event.target.value)}
            disabled={!canManage}
            className={TEXTAREA_CLASS}
          />
        </div>
        {renderSave("network", () => ({
          allowed_hosts: hosts
            .split("\n")
            .map((host) => host.trim())
            .filter(Boolean),
        }))}
      </section>

      <section className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-secondary">{t("project_settings.agents.sandbox.web.title")}</h4>
        {[
          {
            key: "search",
            checked: websearch,
            onChange: setWebsearch,
            label: t("project_settings.agents.sandbox.web.search"),
            hint: t("project_settings.agents.sandbox.web.search_hint"),
          },
          {
            key: "fetch",
            checked: webfetch,
            onChange: setWebfetch,
            label: t("project_settings.agents.sandbox.web.fetch"),
            hint: t("project_settings.agents.sandbox.web.fetch_hint"),
          },
        ].map(({ key, checked, onChange, label, hint }) => (
          <div key={key} className="flex items-center justify-between gap-4">
            <div className="flex flex-col gap-1">
              <span className="text-body-sm-regular text-secondary">{label}</span>
              <span className="text-body-xs-regular text-tertiary">{hint}</span>
            </div>
            <Switch size="sm" checked={checked} onCheckedChange={onChange} aria-label={label} disabled={!canManage} />
          </div>
        ))}
        {renderSave("web", () => ({ websearch_enabled: websearch, webfetch_enabled: webfetch }))}
      </section>

      <section className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-secondary">{t("project_settings.agents.sandbox.skills.title")}</h4>
        <p className="text-body-xs-regular text-tertiary">{t("project_settings.agents.sandbox.skills.hint")}</p>
        {skills.length === 0 && (
          <p className="text-body-sm-regular text-tertiary">{t("project_settings.agents.sandbox.skills.empty")}</p>
        )}
        {skills.map((skill, index) => (
          <div
            key={skill.name}
            className="flex flex-col gap-3 rounded-md border border-subtle p-4 md:flex-row md:items-center md:justify-between"
          >
            <div className="flex min-w-0 flex-col gap-1">
              <span className="text-body-sm-medium text-primary">{skill.name}</span>
              <span className="text-body-xs-regular text-tertiary">{skill.description}</span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Switch
                size="sm"
                checked={skill.enabled}
                onCheckedChange={(enabled) =>
                  setSkills((current) =>
                    current.map((item, itemIndex) => (itemIndex === index ? { ...item, enabled } : item))
                  )
                }
                aria-label={skill.name}
                disabled={!canManage}
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("project_settings.agents.sandbox.edit")}
                disabled={!canManage}
                onClick={() => setSkillDraft({ index, ...skill })}
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("project_settings.agents.sandbox.remove")}
                disabled={!canManage}
                onClick={() => setSkills((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              />
            </div>
          </div>
        ))}
        <div>
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            label={t("project_settings.agents.sandbox.add")}
            disabled={!canManage}
            onClick={() => setSkillDraft({ index: null, name: "", description: "", content: "", enabled: true })}
          />
        </div>
        {skillDraft && (
          <div className="flex flex-col gap-3 rounded-md border border-subtle p-4">
            <InputField
              size="lg"
              orientation="vertical"
              name="skill_name"
              label={t("project_settings.agents.sandbox.name")}
              value={skillDraft.name}
              onChange={(event) => setSkillDraft({ ...skillDraft, name: event.target.value })}
              disabled={!canManage}
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="skill_description"
              label={t("project_settings.agents.sandbox.description")}
              value={skillDraft.description}
              onChange={(event) => setSkillDraft({ ...skillDraft, description: event.target.value })}
              disabled={!canManage}
            />
            <div className="flex flex-col gap-1">
              <label htmlFor="agent-sandbox-skill-content" className="text-body-xs-regular text-secondary">
                {t("project_settings.agents.sandbox.skills.content")}
              </label>
              <textarea
                id="agent-sandbox-skill-content"
                rows={6}
                value={skillDraft.content}
                onChange={(event) => setSkillDraft({ ...skillDraft, content: event.target.value })}
                disabled={!canManage}
                className={`${TEXTAREA_CLASS} font-mono`}
              />
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="secondary"
                size="md"
                stretch="auto"
                label={t("project_settings.agents.sandbox.cancel")}
                onClick={() => setSkillDraft(null)}
              />
              <Button
                variant="primary"
                size="md"
                stretch="auto"
                label={
                  skillDraft.index === null
                    ? t("project_settings.agents.sandbox.add")
                    : t("project_settings.agents.sandbox.save")
                }
                disabled={!canManage || !skillDraft.name.trim()}
                onClick={commitSkill}
              />
            </div>
          </div>
        )}
        {renderSave("skills", () => ({ skills }))}
      </section>

      <section className="flex flex-col gap-2">
        <h4 className="text-body-sm-medium text-secondary">{t("project_settings.agents.sandbox.mcp.title")}</h4>
        <p className="text-body-xs-regular text-tertiary">{t("project_settings.agents.sandbox.mcp.hint")}</p>
        {servers.length === 0 && (
          <p className="text-body-sm-regular text-tertiary">{t("project_settings.agents.sandbox.mcp.empty")}</p>
        )}
        {servers.map((server, index) => (
          <div
            key={server.name}
            className="flex flex-col gap-3 rounded-md border border-subtle p-4 md:flex-row md:items-center md:justify-between"
          >
            <div className="flex min-w-0 flex-col gap-1">
              <span className="text-body-sm-medium text-primary">{server.name}</span>
              <span className="text-body-xs-regular break-all text-tertiary">{server.url}</span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Switch
                size="sm"
                checked={server.enabled}
                onCheckedChange={(enabled) =>
                  setServers((current) =>
                    current.map((item, itemIndex) => (itemIndex === index ? { ...item, enabled } : item))
                  )
                }
                aria-label={server.name}
                disabled={!canManage}
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("project_settings.agents.sandbox.edit")}
                disabled={!canManage}
                onClick={() => setServerDraft({ index, ...server })}
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("project_settings.agents.sandbox.remove")}
                disabled={!canManage}
                onClick={() => setServers((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              />
            </div>
          </div>
        ))}
        <div>
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            label={t("project_settings.agents.sandbox.add")}
            disabled={!canManage}
            onClick={() => setServerDraft({ index: null, name: "", url: "", enabled: true, headers: [] })}
          />
        </div>
        {serverDraft && (
          <div className="flex flex-col gap-3 rounded-md border border-subtle p-4">
            <InputField
              size="lg"
              orientation="vertical"
              name="mcp_server_name"
              label={t("project_settings.agents.sandbox.name")}
              value={serverDraft.name}
              onChange={(event) => setServerDraft({ ...serverDraft, name: event.target.value })}
              disabled={!canManage}
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="mcp_server_url"
              label={t("project_settings.agents.sandbox.mcp.url")}
              type="url"
              value={serverDraft.url}
              onChange={(event) => setServerDraft({ ...serverDraft, url: event.target.value })}
              disabled={!canManage}
            />
            <div className="flex flex-col gap-2">
              <span className="text-body-xs-regular text-secondary">
                {t("project_settings.agents.sandbox.mcp.headers")}
              </span>
              {serverDraft.headers.map((header, headerIndex) => (
                <div key={header.id} className="flex items-end gap-2">
                  <InputField
                    size="lg"
                    orientation="vertical"
                    name={`mcp_header_name_${headerIndex}`}
                    label={t("project_settings.agents.sandbox.mcp.header_name")}
                    value={header.name}
                    onChange={(event) =>
                      setServerDraft({
                        ...serverDraft,
                        headers: serverDraft.headers.map((item, itemIndex) =>
                          itemIndex === headerIndex
                            ? { ...item, name: event.target.value, saved: item.saved && event.target.value === item.id }
                            : item
                        ),
                      })
                    }
                    disabled={!canManage}
                  />
                  <InputField
                    size="lg"
                    orientation="vertical"
                    name={`mcp_header_value_${headerIndex}`}
                    label={t("project_settings.agents.sandbox.mcp.header_value")}
                    type="password"
                    autoComplete="new-password"
                    value={header.value}
                    placeholder={header.saved ? t("project_settings.agents.sandbox.mcp.saved_placeholder") : undefined}
                    onChange={(event) =>
                      setServerDraft({
                        ...serverDraft,
                        headers: serverDraft.headers.map((item, itemIndex) =>
                          itemIndex === headerIndex ? { ...item, value: event.target.value } : item
                        ),
                      })
                    }
                    disabled={!canManage}
                  />
                  <Button
                    variant="secondary"
                    size="sm"
                    stretch="auto"
                    label={t("project_settings.agents.sandbox.remove")}
                    disabled={!canManage}
                    onClick={() =>
                      setServerDraft({
                        ...serverDraft,
                        headers: serverDraft.headers.filter((_, itemIndex) => itemIndex !== headerIndex),
                      })
                    }
                  />
                </div>
              ))}
              <div>
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  label={t("project_settings.agents.sandbox.mcp.add_header")}
                  disabled={!canManage}
                  onClick={() =>
                    setServerDraft({
                      ...serverDraft,
                      headers: [
                        ...serverDraft.headers,
                        { id: `new-${nextHeaderId++}`, name: "", value: "", saved: false },
                      ],
                    })
                  }
                />
              </div>
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="secondary"
                size="md"
                stretch="auto"
                label={t("project_settings.agents.sandbox.cancel")}
                onClick={() => setServerDraft(null)}
              />
              <Button
                variant="primary"
                size="md"
                stretch="auto"
                label={
                  serverDraft.index === null
                    ? t("project_settings.agents.sandbox.add")
                    : t("project_settings.agents.sandbox.save")
                }
                disabled={!canManage || !serverDraft.name.trim()}
                onClick={commitServer}
              />
            </div>
          </div>
        )}
        {renderSave("mcp", () => ({
          // Untouched servers still send their header names with an empty value, which keeps the stored secret.
          mcp_servers: servers.map((server) => ({
            name: server.name,
            url: server.url,
            enabled: server.enabled,
            headers: Object.fromEntries(server.headers.map((header) => [header.name, header.value])),
          })),
        }))}
      </section>
    </div>
  );
}
