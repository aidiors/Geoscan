import { useRef, useState } from "react";
import { errorMessage } from "@/shared/api/errorMessage";
import { download } from "@/shared/browser/download";
import { parseSavedScenario } from "@/features/planner/storage/validateScenario";
import { HttpProjectRepository } from "@/features/projects/api/ProjectRepository";
import type { ProjectDocument, ProjectRevision, ProjectSummary } from "./types";

export interface ProjectWorkspace {
  capture: () => ProjectDocument;
  restore: (document: ProjectDocument) => void;
  onStatus: (message: string) => void;
}
const repository = new HttpProjectRepository();

export function useProjects({ capture, restore, onStatus }: ProjectWorkspace) {
  const [name, setName] = useState("Новый проект");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [history, setHistory] = useState<ProjectSummary[]>([]);
  const [revision, setRevision] = useState("");
  const [opened, setOpened] = useState<{ id: string; revision: number; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try { await action(); } catch (error) { onStatus(errorMessage(error)); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function refresh() {
    setProjects(await repository.list());
  }
  function apply(result: ProjectRevision) {
    restore(result.document);
    setName(result.name); setProjectId(result.project_id); setOpened({ id: result.project_id, revision: result.revision, name: result.name });
    setRevision(String(result.revision));
    onStatus(`Открыт проект «${result.name}», ревизия ${result.revision}. Сохранённый результат - для анализа, экспорт в автопилот требует нового расчёта.`);
  }
  async function select(id: string) {
    setProjectId(id); setRevision(""); setHistory([]);
    if (id) setHistory(await repository.history(id));
  }
  async function save(asNew: boolean) {
    const document = capture();
    const existing = asNew ? null : opened;
    const result = await repository.save(name, document, existing);
    // Saving never replaces the live, possibly still improving result.
    setOpened({ id: result.project_id, revision: result.revision, name: result.name }); setProjectId(result.project_id);
    await refresh(); await select(result.project_id); setRevision(String(result.revision));
    onStatus(`Проект «${result.name}» сохранён на сервере, ревизия ${result.revision}`);
  }
  async function importProject(file: File) {
    if (file.size > 40 * 1024 * 1024) throw new Error("Файл проекта превышает 40 МиБ");
    const payload: unknown = JSON.parse(await file.text());
    if (!payload || typeof payload !== "object" || !("document" in payload)) throw new Error("Неподдерживаемый файл проекта");
    const document = payload.document;
    if (!document || typeof document !== "object" || !("editor_state" in document)) throw new Error("В проекте нет настроек редактора");
    parseSavedScenario(document.editor_state);
    const result = await repository.importArchive(payload);
    apply(result); await refresh(); await select(result.project_id);
  }

  async function openRevision() { apply(await repository.get(projectId, revision)); }
  async function exportProject() {
    const portable = await repository.portable(name, capture());
    download(JSON.stringify(portable), "geoplan-project.json", "application/json");
    onStatus("Файл проекта скачан: исходные данные, параметры и сохранённые результаты");
  }
  return { name, setName, projects, projectId, history, revision, setRevision, opened, busy,
    run, refresh, select, save, importProject, openRevision, exportProject };
}
