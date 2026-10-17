import type { DroneProfile, FlightSiteState, Scenario } from "@/features/planner/model/types";
import type { ImportedZone } from "@/shared/api/types";

function polygon(
  zoneId: string,
  name: string,
  zoneType: ImportedZone["zone_type"],
  coordinates: Array<[number, number]>,
): ImportedZone {
  return {
    zone_id: zoneId,
    name,
    zone_type: zoneType,
    geometry_type: "polygon",
    coordinates: coordinates.map(([longitude, latitude]) => ({ longitude, latitude })),
    interior_rings: [],
  };
}

function site(
  siteId: string,
  name: string,
  role: FlightSiteState["role"],
  longitude: number,
  latitude: number,
): FlightSiteState {
  return {
    site_id: siteId,
    name,
    role,
    coordinate: { longitude, latitude },
    assignedDroneId: "",
  };
}

export const initialFleet: DroneProfile[] = [
  {
    id: "201",
    name: "Геоскан 201",
    color: "#6d28d9",
    battery: 100,
    batteryHealth: 100,
    energyConsumptionMultiplier: 1,
    speed: 70,
    flightMinutes: 180,
    maxWind: 12,
    reserve: 20,
    enabled: true,
    tasks: ["rgb", "multispectral"],
    autopilotFirmware: "generic_mavlink",
    airframeType: "fixed_wing",
    hoverSpeed: 5,
    maintenanceFlights: 80,
    maintenanceHours: null,
    completedFlights: 0,
    accumulatedHours: 0,
    maximumClimbRate: null,
    maximumDescentRate: null,
    minimumMissionAltitude: null,
    maximumMissionAltitude: null,
    minimumMissionAltitudeAgl: 100,
    maximumMissionAltitudeAgl: null,
    minimumMissionAltitudeMsl: null,
    maximumMissionAltitudeMsl: 4000,
    minimumCruiseSpeed: 64,
    maximumCruiseSpeed: 130,
    maximumRouteDistance: 210,
    minimumOperatingTemperature: -20,
    maximumOperatingTemperature: 40,
    maximumControlRange: 40,
    minimumTurnaroundMinutes: 0,
    payloads: [
      { payload_id: "201-rgb", name: "Камера Геоскан 201 RGB", supported_tasks: ["rgb"] },
      { payload_id: "201-pollux", name: "Геоскан Pollux", supported_tasks: ["multispectral"] },
    ],
  },
  {
    id: "801",
    name: "Геоскан 801",
    color: "#d946ef",
    battery: 100,
    batteryHealth: 100,
    energyConsumptionMultiplier: 1,
    speed: 54,
    flightMinutes: 40,
    maxWind: 10,
    reserve: 20,
    enabled: true,
    tasks: ["rgb", "thermal", "video"],
    autopilotFirmware: "generic_mavlink",
    airframeType: "quadrotor",
    hoverSpeed: 5,
    maintenanceFlights: null,
    maintenanceHours: 160,
    completedFlights: 0,
    accumulatedHours: 0,
    maximumClimbRate: null,
    maximumDescentRate: null,
    minimumMissionAltitude: null,
    maximumMissionAltitude: null,
    minimumMissionAltitudeAgl: null,
    maximumMissionAltitudeAgl: null,
    minimumMissionAltitudeMsl: null,
    maximumMissionAltitudeMsl: 4000,
    minimumCruiseSpeed: null,
    maximumCruiseSpeed: 54,
    maximumRouteDistance: null,
    minimumOperatingTemperature: -20,
    maximumOperatingTemperature: 40,
    maximumControlRange: 10,
    minimumTurnaroundMinutes: 0,
    payloads: [
      { payload_id: "801-visible", name: "Камера видимого спектра 801", supported_tasks: ["rgb", "video"] },
      { payload_id: "801-thermal", name: "Тепловизор 801", supported_tasks: ["thermal", "video"] },
    ],
  },
  {
    id: "gemini",
    name: "Геоскан Gemini",
    color: "#0f766e",
    battery: 100,
    batteryHealth: 100,
    energyConsumptionMultiplier: 1,
    speed: 43,
    flightMinutes: 40,
    maxWind: 10,
    reserve: 20,
    enabled: true,
    tasks: ["rgb", "multispectral"],
    autopilotFirmware: "generic_mavlink",
    airframeType: "quadrotor",
    hoverSpeed: 5,
    maintenanceFlights: null,
    maintenanceHours: null,
    completedFlights: 0,
    accumulatedHours: 0,
    maximumClimbRate: 5,
    maximumDescentRate: 5,
    minimumMissionAltitude: null,
    maximumMissionAltitude: null,
    minimumMissionAltitudeAgl: null,
    maximumMissionAltitudeAgl: 500,
    minimumMissionAltitudeMsl: null,
    maximumMissionAltitudeMsl: 4000,
    minimumCruiseSpeed: null,
    maximumCruiseSpeed: 54,
    maximumRouteDistance: null,
    minimumOperatingTemperature: -15,
    maximumOperatingTemperature: 40,
    maximumControlRange: 5,
    minimumTurnaroundMinutes: 0,
    payloads: [
      { payload_id: "gemini-pf1b", name: "Geoscan PF1B", supported_tasks: ["rgb"] },
      { payload_id: "gemini-pollux", name: "Геоскан Pollux", supported_tasks: ["multispectral"] },
    ],
  },
];

export const scenarios: Scenario[] = [
  {
    id: "urban-rgb",
    title: "Городская ортофотосъёмка",
    description: "RGB-съёмка кварталов с галсами и обходом запретной зоны.",
    task: "rgb",
    altitude: 120,
    workAreas: [polygon("urban-target", "Целевая зона городской съёмки", "work_area", [
      [37.378, 55.686],
      [37.442, 55.686],
      [37.442, 55.717],
      [37.378, 55.717],
    ])],
    noFlyZones: [
      polygon("urban-restricted", "Запретная зона над объектом", "no_fly_zone", [
        [37.407, 55.698],
        [37.415, 55.698],
        [37.415, 55.704],
        [37.407, 55.704],
      ]),
    ],
    segments: [],
    sites: [
      site("urban-takeoff", "Городской старт", "takeoff", 37.39, 55.69),
      site("urban-landing", "Основная посадка", "landing", 37.39, 55.69),
      site("urban-alternate", "Резервная площадка", "alternate", 37.385, 55.685),
    ],
  },
  {
    id: "agro-multispectral",
    title: "Агромониторинг",
    description: "Мультиспектральная съёмка массива с равномерным покрытием.",
    task: "multispectral",
    altitude: 100,
    workAreas: [polygon("agro-target", "Сельскохозяйственный массив", "work_area", [
      [39.684, 54.596],
      [39.746, 54.596],
      [39.746, 54.629],
      [39.684, 54.629],
    ])],
    noFlyZones: [
      polygon("agro-restricted", "Охраняемая природная зона", "no_fly_zone", [
        [39.712, 54.606],
        [39.721, 54.606],
        [39.721, 54.613],
        [39.712, 54.613],
      ]),
    ],
    segments: [],
    sites: [
      site("agro-takeoff", "Старт у поля", "takeoff", 39.688, 54.598),
      site("agro-landing", "Посадка у поля", "landing", 39.688, 54.598),
      site("agro-alternate", "Резервная площадка", "alternate", 39.742, 54.6),
    ],
  },
  {
    id: "thermal-inspection",
    title: "Тепловизионный осмотр",
    description: "Тепловизионная инспекция линейной инфраструктуры.",
    task: "thermal",
    altitude: 80,
    workAreas: [polygon("thermal-target", "Коридор тепловизионного осмотра", "work_area", [
      [37.594, 55.736],
      [37.646, 55.736],
      [37.646, 55.75],
      [37.594, 55.75],
    ])],
    noFlyZones: [
      polygon("thermal-restricted", "Запретная зона инфраструктуры", "no_fly_zone", [
        [37.616, 55.741],
        [37.625, 55.741],
        [37.625, 55.746],
        [37.616, 55.746],
      ]),
    ],
    segments: [],
    sites: [
      site("thermal-takeoff", "Старт инспекции", "takeoff", 37.596, 55.738),
      site("thermal-landing", "Финиш инспекции", "landing", 37.644, 55.738),
      site("thermal-alternate", "Резервная площадка", "alternate", 37.62, 55.749),
    ],
  },
  {
    id: "lidar-corridor",
    title: "LiDAR-коридор",
    description: "Покрытие протяжённого коридора аппаратами с LiDAR-профилем.",
    task: "lidar",
    altitude: 110,
    workAreas: [polygon("lidar-target", "Целевой LiDAR-коридор", "work_area", [
      [30.19, 59.894],
      [30.41, 59.894],
      [30.41, 59.936],
      [30.19, 59.936],
    ])],
    noFlyZones: [
      polygon("lidar-restricted", "Запретная зона аэродрома", "no_fly_zone", [
        [30.292, 59.904],
        [30.31, 59.904],
        [30.31, 59.916],
        [30.292, 59.916],
      ]),
    ],
    segments: [],
    sites: [
      site("lidar-takeoff", "Западный старт", "takeoff", 30.195, 59.898),
      site("lidar-landing", "Восточная посадка", "landing", 30.405, 59.898),
      site("lidar-alternate", "Резервная площадка", "alternate", 30.3, 59.934),
    ],
  },
  {
    id: "multi-urban-districts",
    title: "Несколько городских кварталов",
    description: "Три раздельные рабочие зоны и четыре городских ограничения для проверки распределения вылетов.",
    task: "rgb",
    altitude: 120,
    workAreas: [
      polygon("multi-urban-west", "Западный квартал", "work_area", [
        [37.378, 55.689],
        [37.4, 55.689],
        [37.4, 55.704],
        [37.378, 55.704],
      ]),
      polygon("multi-urban-center", "Центральный квартал", "work_area", [
        [37.405, 55.687],
        [37.428, 55.687],
        [37.428, 55.701],
        [37.405, 55.701],
      ]),
      polygon("multi-urban-north", "Северный квартал", "work_area", [
        [37.395, 55.707],
        [37.42, 55.707],
        [37.42, 55.718],
        [37.395, 55.718],
      ]),
    ],
    noFlyZones: [
      polygon("multi-urban-school", "Школа", "no_fly_zone", [
        [37.386, 55.695],
        [37.392, 55.695],
        [37.392, 55.7],
        [37.386, 55.7],
      ]),
      polygon("multi-urban-government", "Административный объект", "no_fly_zone", [
        [37.414, 55.691],
        [37.421, 55.691],
        [37.421, 55.697],
        [37.414, 55.697],
      ]),
      polygon("multi-urban-hospital", "Больничный комплекс", "no_fly_zone", [
        [37.403, 55.711],
        [37.411, 55.711],
        [37.411, 55.716],
        [37.403, 55.716],
      ]),
      polygon("multi-urban-transit", "Временное ограничение", "no_fly_zone", [
        [37.399, 55.702],
        [37.406, 55.702],
        [37.406, 55.708],
        [37.399, 55.708],
      ]),
    ],
    segments: [],
    sites: [
      site("multi-urban-takeoff-west", "Западный старт", "takeoff", 37.376, 55.687),
      site("multi-urban-takeoff-east", "Восточный старт", "takeoff", 37.431, 55.685),
      site("multi-urban-landing-west", "Западная посадка", "landing", 37.376, 55.687),
      site("multi-urban-landing-east", "Восточная посадка", "landing", 37.431, 55.685),
      site("multi-urban-alternate", "Северная резервная площадка", "alternate", 37.423, 55.72),
    ],
  },
  {
    id: "multi-agro-fields",
    title: "Раздельные агрополя",
    description: "Четыре рабочих поля, природные ограничения и площадки по обе стороны массива.",
    task: "multispectral",
    altitude: 100,
    workAreas: [
      polygon("multi-agro-west", "Западное поле", "work_area", [
        [39.68, 54.596],
        [39.704, 54.594],
        [39.707, 54.609],
        [39.683, 54.612],
      ]),
      polygon("multi-agro-north", "Северное поле", "work_area", [
        [39.71, 54.614],
        [39.735, 54.613],
        [39.737, 54.629],
        [39.712, 54.631],
      ]),
      polygon("multi-agro-east", "Восточное поле", "work_area", [
        [39.741, 54.598],
        [39.765, 54.6],
        [39.762, 54.616],
        [39.738, 54.614],
      ]),
      polygon("multi-agro-south", "Южное поле", "work_area", [
        [39.709, 54.589],
        [39.733, 54.59],
        [39.731, 54.605],
        [39.707, 54.604],
      ]),
    ],
    noFlyZones: [
      polygon("multi-agro-village", "Населённый пункт", "no_fly_zone", [
        [39.696, 54.6],
        [39.703, 54.6],
        [39.703, 54.606],
        [39.696, 54.606],
      ]),
      polygon("multi-agro-reserve", "Охраняемая роща", "no_fly_zone", [
        [39.718, 54.618],
        [39.727, 54.618],
        [39.727, 54.625],
        [39.718, 54.625],
      ]),
      polygon("multi-agro-power", "Энергообъект", "no_fly_zone", [
        [39.747, 54.604],
        [39.755, 54.604],
        [39.755, 54.61],
        [39.747, 54.61],
      ]),
    ],
    segments: [],
    sites: [
      site("multi-agro-takeoff-west", "Западная полевая база", "takeoff", 39.677, 54.592),
      site("multi-agro-takeoff-east", "Восточная полевая база", "takeoff", 39.768, 54.597),
      site("multi-agro-landing-west", "Посадка на западной базе", "landing", 39.677, 54.592),
      site("multi-agro-landing-east", "Посадка на восточной базе", "landing", 39.768, 54.597),
      site("multi-agro-alternate", "Резервная северная площадка", "alternate", 39.74, 54.633),
    ],
  },
  {
    id: "complex-restrictions",
    title: "Сложные пересекающиеся ограничения",
    description: "Три LiDAR-зоны и пять ограничений, включая два пересекающихся контура между участками.",
    task: "lidar",
    altitude: 110,
    workAreas: [
      polygon("complex-west", "Западный участок", "work_area", [
        [30.23, 59.9],
        [30.265, 59.9],
        [30.265, 59.914],
        [30.23, 59.914],
      ]),
      polygon("complex-center", "Центральный участок", "work_area", [
        [30.274, 59.905],
        [30.308, 59.905],
        [30.308, 59.92],
        [30.274, 59.92],
      ]),
      polygon("complex-east", "Восточный участок", "work_area", [
        [30.317, 59.899],
        [30.352, 59.899],
        [30.352, 59.915],
        [30.317, 59.915],
      ]),
    ],
    noFlyZones: [
      polygon("complex-west-facility", "Объект на западном участке", "no_fly_zone", [
        [30.244, 59.905],
        [30.254, 59.905],
        [30.254, 59.911],
        [30.244, 59.911],
      ]),
      polygon("complex-center-facility", "Объект на центральном участке", "no_fly_zone", [
        [30.286, 59.911],
        [30.296, 59.911],
        [30.296, 59.917],
        [30.286, 59.917],
      ]),
      polygon("complex-east-facility", "Объект на восточном участке", "no_fly_zone", [
        [30.33, 59.905],
        [30.34, 59.905],
        [30.34, 59.911],
        [30.33, 59.911],
      ]),
      polygon("complex-overlap-a", "Временное ограничение A", "no_fly_zone", [
        [30.259, 59.91],
        [30.282, 59.91],
        [30.282, 59.917],
        [30.259, 59.917],
      ]),
      polygon("complex-overlap-b", "Временное ограничение B", "no_fly_zone", [
        [30.27, 59.914],
        [30.29, 59.914],
        [30.29, 59.921],
        [30.27, 59.921],
      ]),
    ],
    segments: [],
    sites: [
      site("complex-takeoff-west", "Западный старт", "takeoff", 30.225, 59.897),
      site("complex-takeoff-east", "Восточный старт", "takeoff", 30.357, 59.897),
      site("complex-landing-west", "Западная посадка", "landing", 30.225, 59.897),
      site("complex-landing-east", "Восточная посадка", "landing", 30.357, 59.897),
      site("complex-alternate", "Резервная северная площадка", "alternate", 30.31, 59.924),
    ],
  },
];

export function cloneFleet(): DroneProfile[] {
  return initialFleet.map((drone) => ({ ...drone, tasks: [...drone.tasks] }));
}

export function cloneSites(scenario: Scenario): FlightSiteState[] {
  return scenario.sites.map((flightSite) => ({
    ...flightSite,
    coordinate: { ...flightSite.coordinate },
  }));
}

// Synthetic holdout geometry shared in scale with the backend coverage experiments.
const circleCoordinates: Array<[number, number]> = Array.from({ length: 64 }, (_, index) => {
  const angle = 2 * Math.PI * index / 64;
  return [37.62 + 720 * Math.cos(angle) / (111320 * Math.cos(55.75 * Math.PI / 180)),
    55.75 + 720 * Math.sin(angle) / 110574];
});
const circleHome: [number, number] = [37.62 - 860 / (111320 * Math.cos(55.75 * Math.PI / 180)),
  55.75 - 860 / 110574];
scenarios.push({
  id: "freeform-circle", title: "Свободное покрытие округлой территории",
  description: "Сравнение прямых проходов, контуров и смешанных маршрутов на синтетической области.",
  task: "rgb", altitude: 100,
  workAreas: [polygon("freeform-target", "Округлая территория", "work_area", circleCoordinates)],
  noFlyZones: [], segments: [],
  sites: [site("freeform-start", "Старт", "takeoff", ...circleHome),
    site("freeform-land", "Посадка", "landing", ...circleHome)],
});
