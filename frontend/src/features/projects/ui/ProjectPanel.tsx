import { useProjects } from "@/features/projects/model/useProjects";
import type { ProjectWorkspace } from "@/features/projects/model/useProjects";

export function ProjectPanel(props: ProjectWorkspace) {
  const { name, setName, projects, projectId, history, revision, setRevision, opened, busy,
    run, refresh, select, save, importProject, openRevision, exportProject } = useProjects(props);
  return <details className="project-panel" onToggle={(event) => { if (event.currentTarget.open) void run(refresh); }}>
    <summary>Проекты и история</summary>
    <label>Название проекта<input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} /></label>
    <div className="mini-actions">
      <button type="button" disabled={busy || !name.trim()} onClick={() => void run(() => save(true))}>Сохранить новый проект</button>
      <button type="button" disabled={busy || !opened || !name.trim()} onClick={() => void run(() => save(false))}>Сохранить ревизию</button>
    </div>
    {opened && <p className="hint">Рабочая копия «{opened.name}» · ревизия {opened.revision}. Сохранение добавляет новую ревизию.</p>}
    <label>Сохранённые проекты<select aria-label="Сохранённые проекты" value={projectId} disabled={busy} onChange={(event) => void run(() => select(event.target.value))}>
      <option value="">Выберите проект</option>
      {projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name} · {project.latest_revision}</option>)}
    </select></label>
    <label>Ревизия проекта<select aria-label="Ревизия проекта" value={revision} disabled={busy || !projectId} onChange={(event) => setRevision(event.target.value)}>
      <option value="">Последняя</option>
      {history.map((entry) => <option key={entry.latest_revision} value={entry.latest_revision}>{entry.latest_revision} · {new Date(entry.updated_at).toLocaleString()}</option>)}
    </select></label>
    <div className="mini-actions">
      <button type="button" disabled={busy || !projectId} onClick={() => void run(openRevision)}>Открыть ревизию</button>
      <button type="button" disabled={busy} onClick={() => void run(refresh)}>Обновить список</button>
    </div>
    <div className="mini-actions">
      <button type="button" disabled={busy || !name.trim()} onClick={() => void run(exportProject)}>Скачать проект</button>
    </div>
    <label>Импорт проекта<input type="file" accept=".json" disabled={busy} onChange={(event) => {
      const file = event.target.files?.[0]; event.target.value = "";
      if (file) void run(() => importProject(file));
    }} /></label>
    <p className="hint">Ревизии хранятся на этом сервере. Файл проекта позволяет перенести работу и создать резервную копию. Архив не заменяет проверку актуальности условий перед полётом.</p>
  </details>;
}
