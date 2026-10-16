

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

export async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
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
