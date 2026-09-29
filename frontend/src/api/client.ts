import type {
  ExportTextResponse,
  ImportResponse,
  PlanRequest,
  PlanResponse,
  TurnRadiusAnalysisRequest,
  TurnRadiusAnalysisResult,
  ZoneType,
} from "../types";

interface ApiErrorPayload {
  code?: string;
  message?: string;
  recommendations?: Array<{ message?: string }>;
}

export class ApiError extends Error {
  readonly code: string | null;
  readonly recommendations: string[];

  constructor(message: string, code: string | null = null, recommendations: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.recommendations = recommendations;
  }
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new ApiError("Сервер планирования недоступен.");
  }
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      if (!response.ok) {
        throw new ApiError(text);
      }
      throw new ApiError("Сервер вернул некорректный JSON.");
    }
  }
  if (!response.ok) {
    const apiError = payload as ApiErrorPayload | null;
    throw new ApiError(
      apiError?.message ?? "Сервер не смог обработать запрос.",
      apiError?.code ?? null,
      apiError?.recommendations
        ?.map((recommendation) => recommendation.message)
        .filter((message): message is string => typeof message === "string") ?? [],
    );
  }
  return payload as T;
}

export function createPlan(request: PlanRequest): Promise<PlanResponse> {
  return requestJson<PlanResponse>("/v1/plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function analyzeTurnRadii(
  request: TurnRadiusAnalysisRequest,
): Promise<TurnRadiusAnalysisResult> {
  return requestJson<TurnRadiusAnalysisResult>("/v1/turn-radius-analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function importGeodata(
  fileName: string,
  zoneType: ZoneType,
  content: string,
): Promise<ImportResponse> {
  const format = fileName.toLowerCase().endsWith(".kml") ? "kml" : "geojson";
  return requestJson<ImportResponse>(`/v1/imports/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_file_name: fileName, zone_type: zoneType, content }),
  });
}

export async function exportMission(
  plan: PlanResponse,
  droneId: string,
  format: "geojson" | "kml" | "qgroundcontrol" | "mission-planner",
): Promise<void> {
  const payload = await requestJson<ExportTextResponse | Record<string, unknown>>(
    `/v1/exports/${format}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan, drone_id: droneId }),
    },
  );
  if (format === "geojson") {
    download(JSON.stringify(payload, null, 2), `mission-${droneId}.geojson`, "application/geo+json");
    return;
  }
  const textExport = payload as ExportTextResponse;
  const mimeType =
    format === "kml"
      ? "application/vnd.google-earth.kml+xml"
      : format === "qgroundcontrol"
        ? "application/json"
        : "text/plain";
  download(textExport.content, textExport.file_name, mimeType);
}

function download(content: string, fileName: string, mimeType: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}
