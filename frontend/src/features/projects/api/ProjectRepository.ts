import { requestJson } from "@/shared/api/http";
import type { ProjectDocument, ProjectRevision, ProjectSummary } from "@/features/projects/model/types";

export class HttpProjectRepository {
  list(): Promise<ProjectSummary[]> { return requestJson("/v1/projects", { method: "GET" }); }
  history(id: string): Promise<ProjectSummary[]> { return requestJson(`/v1/projects/${id}/revisions`, { method: "GET" }); }
  get(id: string, revision: string): Promise<ProjectRevision> {
    return requestJson(`/v1/projects/${id}${revision ? `?revision=${revision}` : ""}`, { method: "GET" });
  }
  save(name: string, document: ProjectDocument, parent: { id: string; revision: number } | null): Promise<ProjectRevision> {
    return this.post(parent ? `/v1/projects/${parent.id}/revisions` : "/v1/projects", { name, document, parent_revision: parent?.revision ?? null });
  }
  portable(name: string, document: ProjectDocument): Promise<unknown> { return this.post("/v1/projects/portable", { name, document }); }
  importArchive(payload: unknown): Promise<ProjectRevision> { return this.post("/v1/projects/import", payload); }
  private post<T>(url: string, payload: unknown): Promise<T> {
    return requestJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  }
}
