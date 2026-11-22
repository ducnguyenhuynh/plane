/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// local imports
import { BackstageConnectionCard } from "./backstage-connection-card";
import { GitHubConnectionCard } from "./github-connection-card";

export function WorkspaceIntegrationConnections({ workspaceSlug }: { workspaceSlug: string }) {
  return (
    <div className="flex flex-col gap-4 py-6">
      <GitHubConnectionCard workspaceSlug={workspaceSlug} />
      <BackstageConnectionCard workspaceSlug={workspaceSlug} />
    </div>
  );
}
