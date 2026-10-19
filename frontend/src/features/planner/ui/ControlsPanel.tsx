import { BatteryCharging, Database, ShieldCheck, Wind } from "lucide-react";
import { useRef, useState } from "react";

import { scenarios } from "@/features/demos/data/scenarios";
import { ImportedObstacleMapper } from "@/features/geodata/model/obstacles";
import { InstalledCameraFields } from "@/features/planner/ui/InstalledCameraFields";
import { FleetActions } from "@/features/planner/ui/FleetActions";
import { fleetCatalog } from "@/features/planner/model/fleetCatalog";
import type { AirframeType, AutopilotFirmware, ImportedZone, MissionTask, ZoneType } from "@/shared/api/types";
import type { DroneProfile, FlightSiteState, Objective, PlanningSettings, SiteRole, SurveySettings } from "@/features/planner/model/types";

interface ControlsPanelProps {
  scenarioId: string;
  task: MissionTask;
  objective: Objective;
  drones: DroneProfile[];
  selectedDroneId: string;
  sites: FlightSiteState[];
  importedZones: ImportedZone[];
  survey: SurveySettings;
  settings: PlanningSettings;
  pendingSiteRole: SiteRole | null;
  importing: boolean;
  onScenarioChange: (scenarioId: string) => void;
  onTaskChange: (task: MissionTask) => void;
  onObjectiveChange: (objective: Objective) => void;
  onSelectDrone: (droneId: string) => void;
  onUpdateDrone: (droneId: string, patch: Partial<DroneProfile>) => void;
  onAddDrone: (modelId: string, count: number) => void;
  onCloneDrone: () => void;
  onRemoveDrone: () => void;
  onUpdateSurvey: (patch: Partial<SurveySettings>) => void;
  onUpdateSettings: (patch: Partial<PlanningSettings>) => void;
  onArmSite: (role: SiteRole) => void;
  onAssignSite: (siteId: string, droneId: string) => void;
  onRemoveSite: (siteId: string) => void;
  onImport: (file: File, zoneType: ZoneType) => Promise<void>;
  onRemoveZone: (zoneId: string) => void;
  onRemoveInvalidZones: () => void;
  onSelectZone: (zoneId: string, enabled: boolean) => void;
  onSelectAllWorkAreas: (enabled: boolean) => void;
  onSave: () => void;
  onLoad: () => void;
}

const taskLabels: Record<MissionTask, string> = {
  rgb: "RGB-съёмка",
  multispectral: "Мультиспектральная",
  thermal: "ИК / тепловизионная",
  lidar: "LiDAR-съёмка",
  geophysical: "Геофизическая",
  video: "Видеосъёмка",
};

const zoneTypeLabels: Record<ZoneType, string> = {
  work_area: "Рабочая зона",
  no_fly_zone: "Запретная зона",
  allowed_airspace: "Разрешённое пространство",
  obstacle: "Высотные препятствия",
};

const firmwareLabels: Record<AutopilotFirmware, string> = {
  generic_mavlink: "Generic MAVLink",
  ardupilot: "ArduPilot",
  px4: "PX4",
};

const airframeLabels: Record<AirframeType, string> = {
  fixed_wing: "Самолёт",
  quadrotor: "Квадрокоптер",
  hexarotor: "Гексакоптер",
  octorotor: "Октокоптер",
  vtol_fixed_rotor: "VTOL fixed-rotor",
  generic_multirotor: "Мультиротор",
};

const obstacleMapper = new ImportedObstacleMapper();

export function ControlsPanel(props: ControlsPanelProps) {
  const horizontalObstacles = props.importedZones.filter((zone) => zone.zone_type === "obstacle"
    && obstacleMapper.requiresHorizontalAvoidance(zone)).length;
  const [zoneType, setZoneType] = useState<ZoneType>("work_area");
  const [zoneSearch, setZoneSearch] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const selectedDrone = props.drones.find((drone) => drone.id === props.selectedDroneId) ?? props.drones[0];
  const selectedScenario = scenarios.find((scenario) => scenario.id === props.scenarioId) ?? scenarios[0];

  async function handleImport(): Promise<void> {
    if (selectedFile === null) {
      fileInput.current?.click();
      return;
    }
    await props.onImport(selectedFile, zoneType);
    setSelectedFile(null);
    if (fileInput.current) {
      fileInput.current.value = "";
    }
  }

  return (
    <aside className="panel controls-panel">
      <div className="eyebrow"><Database size={15} /> Конструктор сценария</div>
      <h1>{selectedScenario.title}</h1>
      <p className="lead">{selectedScenario.description}</p>

      <PanelSection title="Тестовый сценарий" badge={`${scenarios.length} конфигураций`}>
        <select value={props.scenarioId} onChange={(event) => props.onScenarioChange(event.target.value)}>
          {scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.title}</option>)}
        </select>
        <p className="hint">{selectedScenario.workAreas.length} рабочих зон · {selectedScenario.noFlyZones.length} запретных зон</p>
      </PanelSection>

      <PanelSection title="Тип работ" badge={taskLabels[props.task]}>
        <select value={props.task} onChange={(event) => props.onTaskChange(event.target.value as MissionTask)}>
          {Object.entries(taskLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <NumberField label="Высота задания MSL, м" value={props.settings.altitude} min={20} max={10000} onChange={(altitude) => props.onUpdateSettings({ altitude })} />
        <div className="field-grid">
          <NumberField label="Отметка поверхности MSL, м" value={props.settings.flatGroundElevation ?? 0} min={-500} max={10000} onChange={(flatGroundElevation) => props.onUpdateSettings({ flatGroundElevation })} />
          <NumberField label="Погрешность поверхности ±, м" value={props.settings.terrainVerticalUncertainty ?? 0} min={0} max={500} onChange={(terrainVerticalUncertainty) => props.onUpdateSettings({ terrainVerticalUncertainty })} />
        </div>
        <p className="hint">Высота над поверхностью: {props.settings.altitude - (props.settings.flatGroundElevation ?? 0)} м, погрешность ±{props.settings.terrainVerticalUncertainty ?? 0} м. Поверхность считается плоской; отметка задаётся оператором и не заменяет реальный рельеф.</p>
      </PanelSection>

      <PanelSection title="Параметры съёмки" badge="Галсы">
        <label className="field">
          <span>Направление галсов</span>
          <select
            value={props.survey.directionMode}
            onChange={(event) => props.onUpdateSurvey({ directionMode: event.target.value as SurveySettings["directionMode"] })}
          >
            <option value="automatic">Оптимальное автоматически</option>
            <option value="manual">Задать вручную</option>
          </select>
        </label>
        <div className="field-grid">
          {props.survey.directionMode === "manual" && <NumberField label="Направление, °" value={props.survey.direction} min={0} max={179} onChange={(direction) => props.onUpdateSurvey({ direction })} />}
          <NumberField label="Ширина полосы, м" value={props.survey.stripWidth} min={1} onChange={(stripWidth) => props.onUpdateSurvey({ stripWidth })} />
          <NumberField label="Боковое перекрытие, %" value={props.survey.sideOverlap} min={50} max={94} onChange={(sideOverlap) => props.onUpdateSurvey({ sideOverlap })} />
          <NumberField label="Продольное перекрытие, %" value={props.survey.forwardOverlap} min={0} max={94} onChange={(forwardOverlap) => props.onUpdateSurvey({ forwardOverlap })} />
          <NumberField label="Максимальный GSD, см" value={props.survey.maximumGsd} min={0.1} step={0.1} onChange={(maximumGsd) => props.onUpdateSurvey({ maximumGsd })} />
          <NumberField label="Макс. маршрут, км" value={props.survey.maximumSegmentDistance} min={1} onChange={(maximumSegmentDistance) => props.onUpdateSurvey({ maximumSegmentDistance })} />
          <NumberField label="Матрица, мм" value={props.survey.sensorWidth} min={1} step={0.1} onChange={(sensorWidth) => props.onUpdateSurvey({ sensorWidth })} />
          <NumberField label="Высота матрицы, мм" value={props.survey.sensorHeight} min={1} step={0.1} onChange={(sensorHeight) => props.onUpdateSurvey({ sensorHeight })} />
          <NumberField label="Фокус, мм" value={props.survey.focalLength} min={1} step={0.1} onChange={(focalLength) => props.onUpdateSurvey({ focalLength })} />
          <NumberField label="Ширина кадра, px" value={props.survey.imageWidth} min={1} onChange={(imageWidth) => props.onUpdateSurvey({ imageWidth })} />
          <NumberField label="Высота кадра, px" value={props.survey.imageHeight} min={1} onChange={(imageHeight) => props.onUpdateSurvey({ imageHeight })} />
          <NumberField label="Интервал снимка, с" value={props.survey.triggerInterval} min={0.1} step={0.1} onChange={(triggerInterval) => props.onUpdateSurvey({ triggerInterval })} />
          <NumberField label="Буфер запрета, м" value={props.survey.restrictionMargin} min={0} onChange={(restrictionMargin) => props.onUpdateSurvey({ restrictionMargin })} />
          <NumberField label="Радиус разворота, м" value={props.settings.turnRadius} min={0} max={10000} onChange={(turnRadius) => props.onUpdateSettings({ turnRadius })} />
        </div>
        <p className="hint">0 м — без ограничения. Положительный радиус применяется к галсам, транзиту и подходу к посадке. Проверяется горизонтальная траектория, не взлёт и глиссада.</p>
      </PanelSection>

      <PanelSection title="Взлёт и посадка" badge={props.pendingSiteRole ? "Укажите на карте" : "Точки готовы"}>
        <div className="site-actions">
          <button type="button" className={props.pendingSiteRole === "takeoff" ? "active" : ""} onClick={() => props.onArmSite("takeoff")}>＋ Взлёт</button>
          <button type="button" className={props.pendingSiteRole === "landing" ? "active" : ""} onClick={() => props.onArmSite("landing")}>＋ Посадка</button>
          <button type="button" className={props.pendingSiteRole === "alternate" ? "active" : ""} onClick={() => props.onArmSite("alternate")}>＋ Резерв</button>
        </div>
        <p className="hint">Выберите тип площадки и нажмите на нужное место карты.</p>
        <div className="site-list">
          {props.sites.map((flightSite) => (
            <div className="site-item" key={flightSite.site_id}>
              <span><strong>{flightSite.name}</strong><small>{flightSite.coordinate.latitude.toFixed(5)}, {flightSite.coordinate.longitude.toFixed(5)}</small></span>
              <button type="button" aria-label={`Удалить ${flightSite.name}`} onClick={() => props.onRemoveSite(flightSite.site_id)}>×</button>
              <select value={flightSite.assignedDroneId} onChange={(event) => props.onAssignSite(flightSite.site_id, event.target.value)}>
                <option value="">Весь парк</option>
                {props.drones.map((drone) => <option key={drone.id} value={drone.id}>{drone.name}</option>)}
              </select>
            </div>
          ))}
        </div>
      </PanelSection>

      <PanelSection title="Ветер" badge="Учитывается">
        <div className="constraint"><Wind size={17} /><span>Постоянный профиль на всю миссию</span></div>
        <div className="field-grid">
          <NumberField label="Скорость, м/с" value={props.settings.windSpeed} min={0} max={100} onChange={(windSpeed) => props.onUpdateSettings({ windSpeed })} />
          <NumberField label="Откуда дует, °" value={props.settings.windDirection} min={0} max={359} onChange={(windDirection) => props.onUpdateSettings({ windDirection })} />
          <NumberField label="Температура, °C" value={props.settings.ambientTemperature} min={-100} max={100} onChange={(ambientTemperature) => props.onUpdateSettings({ ambientTemperature })} />
        </div>
      </PanelSection>

      <PanelSection title="Критерий оптимизации">
        <div className="criteria">
          <CriterionButton value="time" current={props.objective} label="Быстрее" onChange={props.onObjectiveChange} />
          <CriterionButton value="flight" current={props.objective} label="Суммарный налёт" onChange={props.onObjectiveChange} />
          <CriterionButton value="balanced" current={props.objective} label="К сроку" onChange={props.onObjectiveChange} />
        </div>
        <p className="hint">{objectiveHint(props.objective)}</p>
        <label className="solver-option">
          <input
            type="checkbox"
            checked={props.settings.productiveRouting}
            disabled={props.settings.fastHeuristic}
            onChange={(event) => props.onUpdateSettings({ productiveRouting: event.target.checked })}
          />
          <span><b>Оптимизация вылетов</b><small>Сравнивать порядок галсов и разбиения между аппаратами по итоговому расписанию</small></span>
        </label>
        {props.settings.productiveRouting && <p className="hint">{props.settings.fastHeuristic ? "Выключите быстрый расчёт, чтобы сравнивать варианты вылетов." : "«Макс. маршрут» задаёт исходное разбиение для сравнения. Новые вылеты ограничены запасом АКБ, дальностью БВС и условиями задания."}</p>}
        <p className="hint">Сервис автоматически выбирает поиск распределения и сравнивает проверенные расписания. Для нескольких областей оптимизируется общий план.</p>
        <label className="solver-option">
          <input
            type="checkbox"
            checked={props.settings.fastHeuristic}
            onChange={(event) => props.onUpdateSettings({ fastHeuristic: event.target.checked })}
          />
          <span><b>Быстрый расчёт</b><small>Использовать простую эвристику вместо основного оптимизатора</small></span>
        </label>
        <div className="field-grid">
          <TextNumberField label="Срок, мин" value={props.settings.deadline} placeholder="Без срока" onChange={(deadline) => props.onUpdateSettings({ deadline })} />
          <TextNumberField label="Макс. БВС" value={props.settings.maximumActiveDrones} placeholder="Весь парк" integer onChange={(maximumActiveDrones) => props.onUpdateSettings({ maximumActiveDrones })} />
          <NumberField label="Бюджет расчёта, с" value={props.settings.planningTimeLimitSeconds ?? 270} min={1} max={270} onChange={(planningTimeLimitSeconds) => props.onUpdateSettings({ planningTimeLimitSeconds })} />
        </div>
      </PanelSection>

      <PanelSection title="Ограничения" badge="Проверяются полностью">
        <div className="constraint"><ShieldCheck size={17} /><span>Все отрезки проверяются по запретным зонам</span></div>
        <div className="constraint"><BatteryCharging size={17} /><span>Резерв заряда задаётся для каждого БВС</span></div>
        <div className="field-grid">
          <NumberField label="Разведение БВС, м" value={props.settings.minimumSeparation} min={1} max={10000} onChange={(minimumSeparation) => props.onUpdateSettings({ minimumSeparation })} />
          <NumberField label="Вертикальное разделение, м" value={props.settings.minimumVerticalSeparation} min={1} max={1000} onChange={(minimumVerticalSeparation) => props.onUpdateSettings({ minimumVerticalSeparation })} />
          <NumberField label="Шаг эшелона, м" value={props.settings.verticalDeconflictionStep} min={1} max={500} onChange={(verticalDeconflictionStep) => props.onUpdateSettings({ verticalDeconflictionStep })} />
          <NumberField label="Макс. повышение, м" value={props.settings.maximumVerticalDeconfliction} min={0} max={1000} onChange={(maximumVerticalDeconfliction) => props.onUpdateSettings({ maximumVerticalDeconfliction })} />
          <NumberField label="Между вылетами, мин" value={props.settings.turnaroundMinutes} min={0} max={1440} onChange={(turnaroundMinutes) => props.onUpdateSettings({ turnaroundMinutes })} />
          <NumberField label="Ошибка позиции ±, м" value={props.settings.positionUncertainty} min={0} max={1000} onChange={(positionUncertainty) => props.onUpdateSettings({ positionUncertainty })} />
          <NumberField label="Ошибка старта ±, с" value={props.settings.startTimeUncertainty} min={0} max={300} onChange={(startTimeUncertainty) => props.onUpdateSettings({ startTimeUncertainty })} />
          <NumberField label="Ошибка скорости ±, %" value={props.settings.speedUncertainty} min={0} max={50} step={0.1} onChange={(speedUncertainty) => props.onUpdateSettings({ speedUncertainty })} />
          <NumberField label="Занятость площадки, с" value={props.settings.flightSiteOccupancy} min={0} max={600} onChange={(flightSiteOccupancy) => props.onUpdateSettings({ flightSiteOccupancy })} />
          <label className="field">
            <span>Начало плана</span>
            <input type="datetime-local" value={props.settings.planningStart} onChange={(event) => props.onUpdateSettings({ planningStart: event.target.value })} />
          </label>
          <label className="field">
            <span>Окончание работ / светового окна</span>
            <input type="datetime-local" value={props.settings.planningEnd} onChange={(event) => props.onUpdateSettings({ planningEnd: event.target.value })} />
          </label>
        </div>
        <p className="hint">Начало обязательно для временных зон. Окончание ограничивает полёты рабочим или световым окном. Эшелонирование выключено при максимальном повышении 0 м.</p>
      </PanelSection>

      <PanelSection title="Парк БВС" badge={`${props.drones.filter((drone) => drone.enabled).length} в расчёте / ${props.drones.length}`}>
        <div className="fleet-list">
          {props.drones.map((drone) => (
            <button type="button" key={drone.id} className={`fleet-row ${drone.id === props.selectedDroneId ? "active" : ""}`} onClick={() => props.onSelectDrone(drone.id)}>
              <span className="color-dot" style={{ background: drone.color }} />
              <span className="row-name">{drone.name}</span>
              <span className={drone.enabled ? "fleet-enabled" : "fleet-disabled"}>{drone.enabled ? "В расчёте" : "Отключён"}</span>
            </button>
          ))}
        </div>
        {selectedDrone && (
          <div className="editor-card">
            <label className="checkbox-row">
              <input type="checkbox" checked={selectedDrone.enabled} onChange={(event) => props.onUpdateDrone(selectedDrone.id, { enabled: event.target.checked })} />
              <span>Использовать в расчёте</span>
            </label>
            <label>Название<input value={selectedDrone.name} onChange={(event) => props.onUpdateDrone(selectedDrone.id, { name: event.target.value })} /></label>
            <p className="hint">Модель: {fleetCatalog.models.find((model) => model.id === selectedDrone.modelId)?.name ?? "Своя конфигурация"}. ID борта: {selectedDrone.id}</p>
            <NumberField label="Радиус разворота этого БВС, м" value={selectedDrone.minimumTurnRadius ?? 0} min={0} max={10000} onChange={(minimumTurnRadius) => props.onUpdateDrone(selectedDrone.id, { minimumTurnRadius })} />
            <label className="checkbox-row">
              <input type="checkbox" checked={selectedDrone.installedSensor != null} onChange={(event) => props.onUpdateDrone(selectedDrone.id, { installedSensor: event.target.checked ? {
                sensor_id: `camera-${selectedDrone.id}`, name: `Камера ${selectedDrone.name}`,
                sensor_width_mm: props.survey.sensorWidth, sensor_height_mm: props.survey.sensorHeight,
                focal_length_mm: props.survey.focalLength, image_width_px: props.survey.imageWidth,
                image_height_px: props.survey.imageHeight, minimum_trigger_interval_s: props.survey.triggerInterval,
              } : null })} />
              <span>Отдельная установленная камера</span>
            </label>
            {selectedDrone.installedSensor && <InstalledCameraFields sensor={selectedDrone.installedSensor} onChange={(installedSensor) => props.onUpdateDrone(selectedDrone.id, { installedSensor })} />}
            <div className="field-grid">
              <NumberField label="Текущий заряд, %" value={selectedDrone.battery} min={1} max={100} onChange={(battery) => props.onUpdateDrone(selectedDrone.id, { battery })} />
              <NumberField label="Состояние АКБ, %" value={selectedDrone.batteryHealth} min={1} max={100} onChange={(batteryHealth) => props.onUpdateDrone(selectedDrone.id, { batteryHealth })} />
              <NumberField label="Коэффициент расхода" value={selectedDrone.energyConsumptionMultiplier} min={0.1} max={10} step={0.05} onChange={(energyConsumptionMultiplier) => props.onUpdateDrone(selectedDrone.id, { energyConsumptionMultiplier })} />
              <NumberField label="Скорость, км/ч" value={selectedDrone.speed} min={1} onChange={(speed) => props.onUpdateDrone(selectedDrone.id, { speed })} />
              <NumberField label="Полёт, мин" value={selectedDrone.flightMinutes} min={1} onChange={(flightMinutes) => props.onUpdateDrone(selectedDrone.id, { flightMinutes })} />
              <NumberField label="Ветер, м/с" value={selectedDrone.maxWind} min={1} onChange={(maxWind) => props.onUpdateDrone(selectedDrone.id, { maxWind })} />
              <NumberField label="Резерв, %" value={selectedDrone.reserve} min={0} max={99} onChange={(reserve) => props.onUpdateDrone(selectedDrone.id, { reserve })} />
              <OptionalNumberField label="Готовых комплектов АКБ" value={selectedDrone.availableBatterySets ?? null} min={0} max={10000} integer onChange={(availableBatterySets) => props.onUpdateDrone(selectedDrone.id, { availableBatterySets })} />
              <OptionalNumberField label="Межсервисный ресурс, вылетов" value={selectedDrone.maintenanceFlights} min={1} max={100000} integer onChange={(maintenanceFlights) => props.onUpdateDrone(selectedDrone.id, { maintenanceFlights })} />
              <NumberField label="Вылетов после ТО" value={selectedDrone.completedFlights} min={0} max={100000} onChange={(completedFlights) => props.onUpdateDrone(selectedDrone.id, { completedFlights })} />
              <OptionalNumberField label="Межсервисный ресурс, ч" value={selectedDrone.maintenanceHours} min={0.01} max={100000} onChange={(maintenanceHours) => props.onUpdateDrone(selectedDrone.id, { maintenanceHours })} />
              <NumberField label="Налёт после ТО, ч" value={selectedDrone.accumulatedHours} min={0} max={100000} step={0.1} onChange={(accumulatedHours) => props.onUpdateDrone(selectedDrone.id, { accumulatedHours })} />
            </div>
            <p className="hint">Один готовый комплект АКБ — один вылет с указанным зарядом и состоянием. Пустое поле означает неограниченное число подготовленных комплектов. Зарядка в ходе задания не рассчитывается; время замены задайте в обслуживании между вылетами. Лимиты ТО ограничивают суммарные вылеты и налёт этого борта.</p>
            <div className="field-grid">
              <label className="field">
                <span>Автопилот</span>
                <select value={selectedDrone.autopilotFirmware} onChange={(event) => props.onUpdateDrone(selectedDrone.id, { autopilotFirmware: event.target.value as AutopilotFirmware })}>
                  {Object.entries(firmwareLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Тип аппарата</span>
                <select value={selectedDrone.airframeType} onChange={(event) => props.onUpdateDrone(selectedDrone.id, { airframeType: event.target.value as AirframeType })}>
                  {Object.entries(airframeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <NumberField label="Hover speed QGC, м/с" value={selectedDrone.hoverSpeed} min={0.1} max={100} step={0.1} onChange={(hoverSpeed) => props.onUpdateDrone(selectedDrone.id, { hoverSpeed })} />
              <OptionalNumberField label="Набор, м/с" value={selectedDrone.maximumClimbRate} min={0.1} max={100} onChange={(maximumClimbRate) => props.onUpdateDrone(selectedDrone.id, { maximumClimbRate })} />
              <OptionalNumberField label="Снижение, м/с" value={selectedDrone.maximumDescentRate} min={0.1} max={100} onChange={(maximumDescentRate) => props.onUpdateDrone(selectedDrone.id, { maximumDescentRate })} />
            </div>
            <div className="field-grid">
              <OptionalNumberField label="Мин. высота AGL, м" value={selectedDrone.minimumMissionAltitudeAgl} min={0} max={10000} onChange={(minimumMissionAltitudeAgl) => props.onUpdateDrone(selectedDrone.id, { minimumMissionAltitudeAgl })} />
              <OptionalNumberField label="Макс. высота AGL, м" value={selectedDrone.maximumMissionAltitudeAgl} min={1} max={10000} onChange={(maximumMissionAltitudeAgl) => props.onUpdateDrone(selectedDrone.id, { maximumMissionAltitudeAgl })} />
              <OptionalNumberField label="Мин. высота MSL, м" value={selectedDrone.minimumMissionAltitudeMsl} min={-500} max={100000} onChange={(minimumMissionAltitudeMsl) => props.onUpdateDrone(selectedDrone.id, { minimumMissionAltitudeMsl })} />
              <OptionalNumberField label="Макс. высота MSL, м" value={selectedDrone.maximumMissionAltitudeMsl} min={-499} max={100000} onChange={(maximumMissionAltitudeMsl) => props.onUpdateDrone(selectedDrone.id, { maximumMissionAltitudeMsl })} />
              <OptionalNumberField label="Мин. крейсерская, км/ч" value={selectedDrone.minimumCruiseSpeed} min={0.1} max={300} onChange={(minimumCruiseSpeed) => props.onUpdateDrone(selectedDrone.id, { minimumCruiseSpeed })} />
              <OptionalNumberField label="Макс. крейсерская, км/ч" value={selectedDrone.maximumCruiseSpeed} min={0.1} max={300} onChange={(maximumCruiseSpeed) => props.onUpdateDrone(selectedDrone.id, { maximumCruiseSpeed })} />
              <OptionalNumberField label="Макс. маршрут, км" value={selectedDrone.maximumRouteDistance} min={0.1} max={10000} onChange={(maximumRouteDistance) => props.onUpdateDrone(selectedDrone.id, { maximumRouteDistance })} />
              <OptionalNumberField label="Дальность управления, км" value={selectedDrone.maximumControlRange} min={0.1} max={10000} onChange={(maximumControlRange) => props.onUpdateDrone(selectedDrone.id, { maximumControlRange })} />
              <NumberField label="Мин. обслуживание между вылетами, мин" value={selectedDrone.minimumTurnaroundMinutes} min={0} max={1440} onChange={(minimumTurnaroundMinutes) => props.onUpdateDrone(selectedDrone.id, { minimumTurnaroundMinutes })} />
              <OptionalNumberField label="Мин. температура, °C" value={selectedDrone.minimumOperatingTemperature} min={-100} max={100} onChange={(minimumOperatingTemperature) => props.onUpdateDrone(selectedDrone.id, { minimumOperatingTemperature })} />
              <OptionalNumberField label="Макс. температура, °C" value={selectedDrone.maximumOperatingTemperature} min={-100} max={100} onChange={(maximumOperatingTemperature) => props.onUpdateDrone(selectedDrone.id, { maximumOperatingTemperature })} />
            </div>
            <p className="hint">Профиль используется при экспорте миссии; Generic MAVLink не приписывает аппарату неподтверждённую прошивку.</p>
            <label>Задачи<input value={selectedDrone.tasks.join(", ")} onChange={(event) => {
              const tasks = parseTasks(event.target.value);
              const payloads = selectedDrone.payloads.length === 1 && selectedDrone.payloads[0].payload_id.startsWith("custom-")
                ? [{ ...selectedDrone.payloads[0], supported_tasks: tasks }]
                : selectedDrone.payloads;
              props.onUpdateDrone(selectedDrone.id, { tasks, payloads });
            }} /></label>
          </div>
        )}
        <FleetActions count={props.drones.length} onAdd={props.onAddDrone} onClone={props.onCloneDrone} onRemove={props.onRemoveDrone} />
      </PanelSection>

      <PanelSection title="Геоданные" badge="KML / GeoJSON">
        <select aria-label="Тип импортируемых геоданных" value={zoneType} onChange={(event) => setZoneType(event.target.value as ZoneType)}>
          {Object.entries(zoneTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        {zoneType === "allowed_airspace" && (
          <div className="field-grid">
            <NumberField label="Мин. высота MSL, м" value={props.settings.airspaceMinimumAltitude} min={-500} onChange={(airspaceMinimumAltitude) => props.onUpdateSettings({ airspaceMinimumAltitude })} />
            <NumberField label="Макс. высота MSL, м" value={props.settings.airspaceMaximumAltitude} min={-499} onChange={(airspaceMaximumAltitude) => props.onUpdateSettings({ airspaceMaximumAltitude })} />
          </div>
        )}
        <label className="file-picker">
          <span>Выбрать файл</span><small>{selectedFile?.name ?? "Файл не выбран"}</small>
          <input ref={fileInput} type="file" accept=".geojson,.json,.kml" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
        </label>
        <button className="secondary-button import-button" type="button" disabled={props.importing} onClick={() => void handleImport()}>{props.importing ? "Импортируем…" : selectedFile ? "Импортировать зону" : "Выбрать геоданные"}</button>
        {horizontalObstacles > 0 && <p className="hint" role="status">Препятствий без абсолютной отметки вершины: {horizontalObstacles}. Они обходятся по горизонтали на всех высотах; недостающие высоты не заменяются нулём.</p>}
        {props.importedZones.some((zone) => zone.zone_type === "work_area") && <p className="hint">Рабочие области взяты из импорта; демозапреты исключены. Парк и площадки сохранены — проверьте их соответствие заданию. Отсутствие загруженных запретов не подтверждает их отсутствие на местности.</p>}
        {props.importedZones.some((zone) => zone.metadata?.geometry_issue || zone.metadata?.geometry_warning) && (
          <p className="hint">Без площади: {props.importedZones.filter((zone) => zone.metadata?.geometry_issue).length}; меньше 1 м²: {props.importedZones.filter((zone) => zone.metadata?.geometry_warning).length}. Проверьте исходные данные. <button type="button" onClick={props.onRemoveInvalidZones}>Убрать области без площади и меньше 1 м²</button></p>
        )}
        {props.importedZones.some((zone) => zone.zone_type === "work_area") && (
          <><p className="hint">Выбрано рабочих областей: {props.importedZones.filter((zone) => zone.zone_type === "work_area" && zone.enabled !== false).length}. Расчёт выполняется для выбранного пакета.</p>
            <div className="mini-actions"><button type="button" onClick={() => props.onSelectAllWorkAreas(true)}>Выбрать все</button><button type="button" onClick={() => props.onSelectAllWorkAreas(false)}>Снять выбор</button></div>
            <label className="field"><span>Найти область</span><input value={zoneSearch} onChange={(event) => setZoneSearch(event.target.value)} placeholder="Название или ID" /></label></>
        )}
        <div className="zone-list">
          {props.importedZones.filter((zone) => `${zone.name} ${zone.zone_id}`.toLowerCase().includes(zoneSearch.toLowerCase())).map((zone) => <div className="zone-item" key={zone.zone_id}><span className="zone-selection">{zone.zone_type === "work_area" && <input type="checkbox" aria-label={`Включить область ${zone.name}`} checked={zone.enabled !== false} onChange={(event) => props.onSelectZone(zone.zone_id, event.target.checked)} />}<span><b>{zone.name}</b><small>{zone.metadata?.geometry_issue ?? zone.metadata?.geometry_warning ?? zoneTypeLabels[zone.zone_type]}</small></span></span><button type="button" aria-label={`Удалить ${zone.name}`} onClick={() => props.onRemoveZone(zone.zone_id)}>×</button></div>)}
        </div>
      </PanelSection>

      <div className="mini-actions scenario-actions"><button type="button" onClick={props.onSave}>Сохранить</button><button type="button" onClick={props.onLoad}>Восстановить</button></div>
    </aside>
  );
}

function PanelSection({ title, badge, children }: { title: string; badge?: string; children: React.ReactNode }) {
  return <section className="panel-section"><div className="label-row"><span>{title}</span>{badge && <span className="check">{badge}</span>}</div>{children}</section>;
}

function NumberField({ label, value, min, max, step = 1, onChange }: { label: string; value: number; min?: number; max?: number; step?: number; onChange: (value: number) => void }) {
  return <label className="field"><span>{label}</span><input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function OptionalNumberField({ label, value, min, max, integer = false, onChange }: { label: string; value: number | null; min?: number; max?: number; integer?: boolean; onChange: (value: number | null) => void }) {
  return <label className="field"><span>{label}</span><input type="number" value={value ?? ""} min={min} max={max} step={integer ? 1 : "any"} placeholder="не задано" onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} /></label>;
}

function TextNumberField({ label, value, placeholder, integer = false, onChange }: { label: string; value: string; placeholder: string; integer?: boolean; onChange: (value: string) => void }) {
  return <label className="field"><span>{label}</span><input type="number" value={value} min={1} step={integer ? 1 : "any"} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} /></label>;
}

function CriterionButton({ value, current, label, onChange }: { value: Objective; current: Objective; label: string; onChange: (value: Objective) => void }) {
  return <button type="button" aria-pressed={value === current} onClick={() => onChange(value)}>{label}</button>;
}

function objectiveHint(objective: Objective): string {
  if (objective === "time") return "Минимизируем срок всего проекта, распределяя длинные участки первыми.";
  if (objective === "flight") return "Минимизируем полный налёт с учётом транзита к зоне и обратно.";
  return "Минимизируем налёт среди назначений, укладывающихся в заданный срок.";
}

function parseTasks(value: string): MissionTask[] {
  const known = new Set<MissionTask>(["rgb", "multispectral", "thermal", "lidar", "video", "geophysical"]);
  return value.split(",").map((item) => item.trim()).filter((item): item is MissionTask => known.has(item as MissionTask));
}
