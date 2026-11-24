/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { TAgentRun } from "@plane/types";

const LINK_CLASS = "text-link-primary hover:underline";

const GITHUB_REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

const isGitHubRepository = (repository: string) => GITHUB_REPOSITORY.test(repository);

/** Commit link, or null when the repository is not a plain `owner/name` GitHub slug. */
export const getAgentRunCommitUrl = (repository: string, sha: string): string | null =>
  sha && isGitHubRepository(repository) ? `https://github.com/${repository}/commit/${sha}` : null;

/** A commit only exists on GitHub once the branch is pushed, which happens when the PR opens. */
export const commitUrlForRun = (run: TAgentRun, repository: string, sha: string): string | null =>
  run.pull_request_url && sha ? getAgentRunCommitUrl(repository, sha) : null;

const getBranchUrl = (repository: string, branch: string): string | null =>
  branch && isGitHubRepository(repository)
    ? `https://github.com/${repository}/tree/${branch.split("/").map(encodeURIComponent).join("/")}`
    : null;

const getPullRequestUrl = (url: string): string | null => (url.startsWith("https://github.com/") ? url : null);

type Props = {
  run: TAgentRun;
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-28 shrink-0 text-tertiary">{label}</span>
      <span className="break-all text-secondary">{children}</span>
    </div>
  );
}

export function AgentRunGitSummary({ run }: Props) {
  // translation
  const { t } = useTranslation();

  if (!run.repository) return null;

  const branchUrl = getBranchUrl(run.repository, run.branch);
  const commitUrl = commitUrlForRun(run, run.repository, run.head_sha);
  const pullRequestUrl = getPullRequestUrl(run.pull_request_url);
  const shortSha = run.head_sha.slice(0, 7);

  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-body-sm-medium text-primary">{t("agent_run.git.title")}</h4>
      <div className="flex flex-col gap-1 rounded-md border border-subtle p-3 text-body-xs-regular">
        <Row label={t("agent_run.git.repository")}>{run.repository}</Row>
        {run.branch && (
          <Row label={t("agent_run.git.branch")}>
            {branchUrl ? (
              <a className={LINK_CLASS} href={branchUrl} target="_blank" rel="noopener noreferrer">
                {run.branch}
              </a>
            ) : (
              run.branch
            )}
          </Row>
        )}
        {shortSha && (
          <Row label={t("agent_run.git.commit")}>
            {commitUrl ? (
              <a className={LINK_CLASS} href={commitUrl} target="_blank" rel="noopener noreferrer">
                {shortSha}
              </a>
            ) : (
              shortSha
            )}
          </Row>
        )}
        <Row label={t("agent_run.git.pull_request")}>
          {pullRequestUrl && run.pull_request_number !== null ? (
            <a className={LINK_CLASS} href={pullRequestUrl} target="_blank" rel="noopener noreferrer">
              #{run.pull_request_number}
            </a>
          ) : (
            t("agent_run.git.no_pull_request")
          )}
        </Row>
      </div>
    </section>
  );
}
