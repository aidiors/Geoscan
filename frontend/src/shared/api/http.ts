

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

async function requestResponse(url: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    if (init.signal?.aborted) {
      throw new ApiError("Время ожидания расчёта истекло. Разделите задание на меньшие пакеты и повторите расчёт.");
    }
    throw new ApiError("Сервер планирования недоступен.");
  }
  if (response.ok) return response;
  const payload = await parseJson<ApiErrorPayload>(response);
  throw new ApiError(
    payload?.message ?? "Сервер не смог обработать запрос.",
    payload?.code ?? null,
    payload?.recommendations?.map((item) => item.message)
      .filter((message): message is string => typeof message === "string") ?? [],
  );
}

async function parseJson<T>(response: Response): Promise<T> {
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
  return payload as T;
}

export async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  return parseJson<T>(await requestResponse(url, init));
}

export async function requestBlob(url: string, init: RequestInit): Promise<Blob> {
  return (await requestResponse(url, init)).blob();
}
