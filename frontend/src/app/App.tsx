import { useTheme } from "./theme";
import { ControlsPanel } from "@/features/planner/ui/ControlsPanel";
import { DemoShowcase } from "@/features/demos/ui/DemoShowcase";
import { Header } from "@/app/Header";
import { MissionMap } from "@/widgets/mission-map/MissionMap";
import { ResultsPanel } from "@/features/planner/ui/ResultsPanel";
import { activeWorkAreas } from "@/features/planner/api/buildPlanRequest";
import { usePlanner } from "@/features/planner/model/usePlanner";
import { MissionInsights } from "@/features/planner/ui/MissionInsights";
import { ProjectPanel } from "@/features/projects/ui/ProjectPanel";

export default function App() {
  const [theme, setTheme] = useTheme();
  const {
    scenarioId,
    demoCaseId,
    showcaseOpen,
    setShowcaseOpen,
    task,
    objective,
    setObjective,
    drones,
    selectedDroneId,
    setSelectedDroneId,
    sites,
    setSites,
    importedZones,
    workAreaSource,
    setImportedZones,
    survey,
    setSurvey,
    settings,
    setSettings,
    plan,
    insights,
    handleAdvise,
    handleApplyAdvice,
    canInspectQuality,
    radiusAnalysis,
    analyzingRadii,
    revision,
    status,
    setStatus,
    pendingSiteRole,
    setPendingSiteRole,
    calculating,
    importing,
    exporting,
    scenario,
    mapZones,
    fitKey,
    invalidate,
    handleScenarioChange,
    handleDemoCaseLoad,
    handleTaskChange,
    handleUpdateDrone,
    handleAddDrone,
    handleCloneDrone,
    handleRemoveDrone,
    handleAddSite,
    handleImport,
    handleCalculate,
    handleCancelPlanning,
    handleDemoCaseRun,
    handleAnalyzeRadii,
    handleExport,
    saveScenario,
    loadScenario,
    captureProject,
    restoreProject,
    archivedPlan,
  } = usePlanner();
  return (
    <div className="app-shell">
      <Header status={status} revision={revision} theme={theme} calculating={calculating} onCancelPlanning={handleCancelPlanning} onOpenShowcase={() => setShowcaseOpen(true)} onCalculate={() => void handleCalculate()} onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")} />
      <DemoShowcase
        open={showcaseOpen}
        selectedCaseId={demoCaseId}
        plan={plan}
        calculating={calculating}
        onClose={() => setShowcaseOpen(false)}
        onLoad={handleDemoCaseLoad}
        onRun={(caseId) => void handleDemoCaseRun(caseId)}
      />
      <main className="workspace">
        <ControlsPanel
          scenario={scenario}
          projects={<ProjectPanel capture={captureProject} restore={restoreProject} onStatus={setStatus} />}
          scenarioId={scenarioId}
          task={task}
          objective={objective}
          drones={drones}
          selectedDroneId={selectedDroneId}
          sites={sites}
          importedZones={importedZones}
          survey={survey}
          settings={settings}
          pendingSiteRole={pendingSiteRole}
          importing={importing}
          onScenarioChange={handleScenarioChange}
          onTaskChange={handleTaskChange}
          onObjectiveChange={(nextObjective) => { setObjective(nextObjective); invalidate("Критерий изменён — требуется новый расчёт"); }}
          onSelectDrone={setSelectedDroneId}
          onUpdateDrone={handleUpdateDrone}
          onAddDrone={handleAddDrone}
          onCloneDrone={handleCloneDrone}
          onRemoveDrone={handleRemoveDrone}
          onUpdateSurvey={(patch) => { setSurvey((current) => ({ ...current, ...patch })); invalidate("Параметры съёмки изменены"); }}
          onUpdateSettings={(patch) => { setSettings((current) => ({ ...current, ...patch })); invalidate("Ограничения расчёта изменены"); }}
          onArmSite={(role) => { setPendingSiteRole(role); setStatus("Щёлкните по карте, чтобы добавить площадку"); }}
          onAssignSite={(siteId, droneId) => { setSites((current) => current.map((flightSite) => flightSite.site_id === siteId ? { ...flightSite, assignedDroneId: droneId } : flightSite)); invalidate("Назначение площадки изменено"); }}
          onRemoveSite={(siteId) => { setSites((current) => current.filter((flightSite) => flightSite.site_id !== siteId)); invalidate("Площадка удалена"); }}
          onImport={handleImport}
          onRemoveZone={(zoneId) => { setImportedZones((current) => current.filter((zone) => zone.zone_id !== zoneId)); invalidate("Зона удалена"); }}
          onRemoveInvalidZones={() => { setImportedZones((current) => current.filter((zone) => !zone.metadata?.geometry_issue && !zone.metadata?.geometry_warning)); invalidate("Области без площади и меньше 1 м² удалены из задания"); }}
          onSelectZone={(zoneId, enabled) => { setImportedZones((current) => current.map((zone) => zone.zone_id === zoneId && zone.zone_type === "work_area" ? { ...zone, enabled } : zone)); invalidate("Выбор рабочих областей изменён"); }}
          onSelectAllWorkAreas={(enabled) => { setImportedZones((current) => current.map((zone) => zone.zone_type === "work_area" ? { ...zone, enabled } : zone)); invalidate("Выбор рабочих областей изменён"); }}
          onSave={saveScenario}
          onLoad={loadScenario}
        />
        <MissionMap
          key={fitKey}
          zones={mapZones}
          sites={sites}
          drones={drones}
          plan={plan}
          radiusAnalysis={radiusAnalysis}
          quality={insights.quality}
          selectedDroneId={selectedDroneId}
          pendingSiteRole={pendingSiteRole}
          onAddSite={handleAddSite}
          onClearZones={() => { setImportedZones([]); invalidate("Импортированные зоны очищены"); }}
        />
        <ResultsPanel archived={archivedPlan} insights={<MissionInsights quality={insights.quality} advice={insights.advice} busy={insights.busy || calculating} canInspect={canInspectQuality} onInspect={() => void insights.inspect()} onAdvise={(allowAltitudeChange) => void handleAdvise(allowAltitudeChange)} onApply={handleApplyAdvice} />} plan={plan} drones={drones} selectedDroneId={selectedDroneId} exporting={exporting} workAreas={activeWorkAreas(importedZones, scenario, workAreaSource)} radiusAnalysis={radiusAnalysis} analyzingRadii={analyzingRadii} onAnalyzeRadii={(areaId, minimumRadius, maximumRadius) => void handleAnalyzeRadii(areaId, minimumRadius, maximumRadius)} onSelectDrone={setSelectedDroneId} onExport={(format) => void handleExport(format)} />
      </main>
    </div>
  );
}
