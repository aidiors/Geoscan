import { InstalledCameraFields } from "./InstalledCameraFields";
import { TakeoffLandingFields } from "./TakeoffLandingFields";
import { FixedWingPhaseFields } from "./FixedWingPhaseFields";
import { NumberField, OptionalNumberField } from "./NumberFields";
import { fleetCatalog } from "@/features/planner/model/fleetCatalog";
import type { DroneProfile, SurveySettings } from "@/features/planner/model/types";
import type { AirframeType, AutopilotFirmware, MissionTask } from "@/shared/api/types";

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

interface DroneEditorProps {
  drone: DroneProfile;
  survey: SurveySettings;
  onUpdate: (patch: Partial<DroneProfile>) => void;
}

export function DroneEditor({ drone, survey, onUpdate }: DroneEditorProps) {
  return <div className="drone-editor">
      <details className="fleet-settings-group" open>
        <summary>Основные параметры</summary>
        <div className="fleet-settings-fields">
            <label className="checkbox-row">
              <input type="checkbox" checked={drone.enabled} onChange={(event) => onUpdate({ enabled: event.target.checked })} />
              <span>Использовать в расчёте</span>
            </label>
            <label className="field"><span>Название</span><input value={drone.name} onChange={(event) => onUpdate({ name: event.target.value })} /></label>
            <p className="hint">Модель: {fleetCatalog.models.find((model) => model.id === drone.modelId)?.name ?? "Своя конфигурация"}. ID борта: {drone.id}</p>
            <NumberField label="Радиус разворота этого БВС, м" value={drone.minimumTurnRadius ?? 0} min={0} max={10000} onChange={(minimumTurnRadius) => onUpdate({ minimumTurnRadius })} />
            <div className="field-grid">
              <NumberField label="Текущий заряд, %" value={drone.battery} min={1} max={100} onChange={(battery) => onUpdate({ battery })} />
              <NumberField label="Состояние АКБ, %" value={drone.batteryHealth} min={1} max={100} onChange={(batteryHealth) => onUpdate({ batteryHealth })} />
              <NumberField label="Коэффициент расхода" value={drone.energyConsumptionMultiplier} min={0.1} max={10} step={0.05} onChange={(energyConsumptionMultiplier) => onUpdate({ energyConsumptionMultiplier })} />
              <NumberField label="Скорость, км/ч" value={drone.speed} min={1} onChange={(speed) => onUpdate({ speed })} />
              <NumberField label="Полёт, мин" value={drone.flightMinutes} min={1} onChange={(flightMinutes) => onUpdate({ flightMinutes })} />
              <NumberField label="Ветер, м/с" value={drone.maxWind} min={1} onChange={(maxWind) => onUpdate({ maxWind })} />
              <NumberField label="Резерв, %" value={drone.reserve} min={0} max={99} onChange={(reserve) => onUpdate({ reserve })} />
            </div>
        </div>
      </details>
      <details className="fleet-settings-group">
        <summary>Камера</summary>
        <div className="fleet-settings-fields">
            <label className="checkbox-row">
              <input type="checkbox" checked={drone.installedSensor != null} onChange={(event) => onUpdate({ installedSensor: event.target.checked ? {
                sensor_id: `camera-${drone.id}`, name: `Камера ${drone.name}`,
                sensor_width_mm: survey.sensorWidth, sensor_height_mm: survey.sensorHeight,
                focal_length_mm: survey.focalLength, image_width_px: survey.imageWidth,
                image_height_px: survey.imageHeight, minimum_trigger_interval_s: survey.triggerInterval,
              } : null })} />
              <span>Отдельная установленная камера</span>
            </label>
            {drone.installedSensor && <InstalledCameraFields sensor={drone.installedSensor} onChange={(installedSensor) => onUpdate({ installedSensor })} />}
        </div>
      </details>
      <details className="fleet-settings-group">
        <summary>Ресурс и обслуживание</summary>
        <div className="fleet-settings-fields">
            <div className="field-grid">
              <OptionalNumberField label="Готовых комплектов АКБ" value={drone.availableBatterySets ?? null} min={0} max={10000} integer onChange={(availableBatterySets) => onUpdate({ availableBatterySets })} />
              <OptionalNumberField label="Межсервисный ресурс, вылетов" value={drone.maintenanceFlights} min={1} max={100000} integer onChange={(maintenanceFlights) => onUpdate({ maintenanceFlights })} />
              <NumberField label="Вылетов после ТО" value={drone.completedFlights} min={0} max={100000} onChange={(completedFlights) => onUpdate({ completedFlights })} />
              <OptionalNumberField label="Межсервисный ресурс, ч" value={drone.maintenanceHours} min={0.01} max={100000} onChange={(maintenanceHours) => onUpdate({ maintenanceHours })} />
              <NumberField label="Налёт после ТО, ч" value={drone.accumulatedHours} min={0} max={100000} step={0.1} onChange={(accumulatedHours) => onUpdate({ accumulatedHours })} />
            </div>
            <p className="hint">Один готовый комплект АКБ - один вылет с указанным зарядом и состоянием. Пустое поле означает неограниченное число подготовленных комплектов. Зарядка в ходе задания не рассчитывается, время замены задайте в обслуживании между вылетами. Лимиты ТО ограничивают суммарные вылеты и налёт этого борта.</p>
        </div>
      </details>
      <details className="fleet-settings-group">
        <summary>Лётные ограничения и фазы полёта</summary>
        <div className="fleet-settings-fields">
            <TakeoffLandingFields drone={drone} onUpdate={onUpdate} />
            <FixedWingPhaseFields drone={drone} onUpdate={onUpdate} />
            <div className="field-grid">
              <OptionalNumberField label="Мин. высота AGL, м" value={drone.minimumMissionAltitudeAgl} min={0} max={10000} onChange={(minimumMissionAltitudeAgl) => onUpdate({ minimumMissionAltitudeAgl })} />
              <OptionalNumberField label="Макс. высота AGL, м" value={drone.maximumMissionAltitudeAgl} min={1} max={10000} onChange={(maximumMissionAltitudeAgl) => onUpdate({ maximumMissionAltitudeAgl })} />
              <OptionalNumberField label="Мин. высота MSL, м" value={drone.minimumMissionAltitudeMsl} min={-500} max={100000} onChange={(minimumMissionAltitudeMsl) => onUpdate({ minimumMissionAltitudeMsl })} />
              <OptionalNumberField label="Макс. высота MSL, м" value={drone.maximumMissionAltitudeMsl} min={-499} max={100000} onChange={(maximumMissionAltitudeMsl) => onUpdate({ maximumMissionAltitudeMsl })} />
              <OptionalNumberField label="Мин. крейсерская, км/ч" value={drone.minimumCruiseSpeed} min={0.1} max={300} onChange={(minimumCruiseSpeed) => onUpdate({ minimumCruiseSpeed })} />
              <OptionalNumberField label="Макс. крейсерская, км/ч" value={drone.maximumCruiseSpeed} min={0.1} max={300} onChange={(maximumCruiseSpeed) => onUpdate({ maximumCruiseSpeed })} />
              <OptionalNumberField label="Макс. маршрут, км" value={drone.maximumRouteDistance} min={0.1} max={10000} onChange={(maximumRouteDistance) => onUpdate({ maximumRouteDistance })} />
              <OptionalNumberField label="Дальность управления, км" value={drone.maximumControlRange} min={0.1} max={10000} onChange={(maximumControlRange) => onUpdate({ maximumControlRange })} />
              <NumberField label="Мин. обслуживание между вылетами, мин" value={drone.minimumTurnaroundMinutes} min={0} max={1440} onChange={(minimumTurnaroundMinutes) => onUpdate({ minimumTurnaroundMinutes })} />
              <OptionalNumberField label="Мин. температура, °C" value={drone.minimumOperatingTemperature} min={-100} max={100} onChange={(minimumOperatingTemperature) => onUpdate({ minimumOperatingTemperature })} />
              <OptionalNumberField label="Макс. температура, °C" value={drone.maximumOperatingTemperature} min={-100} max={100} onChange={(maximumOperatingTemperature) => onUpdate({ maximumOperatingTemperature })} />
            </div>
        </div>
      </details>
      <details className="fleet-settings-group">
        <summary>Автопилот и задачи</summary>
        <div className="fleet-settings-fields">
            <div className="field-grid">
              <label className="field">
                <span>Автопилот</span>
                <select value={drone.autopilotFirmware} onChange={(event) => onUpdate({ autopilotFirmware: event.target.value as AutopilotFirmware })}>
                  {Object.entries(firmwareLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Тип аппарата</span>
                <select value={drone.airframeType} onChange={(event) => onUpdate({ airframeType: event.target.value as AirframeType,
                  ...(["fixed_wing", "vtol_fixed_rotor"].includes(event.target.value) ? { takeoffLandingProfile: null } : {}),
                  ...(event.target.value !== "fixed_wing" ? { fixedWingPhaseProfile: null } : {}) })}>
                  {Object.entries(airframeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <NumberField label="Hover speed QGC, м/с" value={drone.hoverSpeed} min={0.1} max={100} step={0.1} onChange={(hoverSpeed) => onUpdate({ hoverSpeed })} />
              <OptionalNumberField label="Предельный набор, м/с" value={drone.maximumClimbRate} min={0.1} max={100} onChange={(maximumClimbRate) => onUpdate({ maximumClimbRate })} />
              <OptionalNumberField label="Предельное снижение, м/с" value={drone.maximumDescentRate} min={0.1} max={100} onChange={(maximumDescentRate) => onUpdate({ maximumDescentRate })} />
            </div>
            <p className="hint">Профиль используется при экспорте миссии, Generic MAVLink не приписывает аппарату неподтверждённую прошивку.</p>
            <label className="field"><span>Задачи</span><input value={drone.tasks.join(", ")} onChange={(event) => {
              const tasks = parseTasks(event.target.value);
              const payloads = drone.payloads.length === 1 && drone.payloads[0].payload_id.startsWith("custom-")
                ? [{ ...drone.payloads[0], supported_tasks: tasks }]
                : drone.payloads;
              onUpdate({ tasks, payloads });
            }} /></label>
        </div>
      </details>
  </div>;
}

function parseTasks(value: string): MissionTask[] {
  const known = new Set<MissionTask>(["rgb", "multispectral", "thermal", "lidar", "video", "geophysical"]);
  return value.split(",").map((item) => item.trim()).filter((item): item is MissionTask => known.has(item as MissionTask));
}
