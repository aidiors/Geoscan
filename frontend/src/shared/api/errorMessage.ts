import { ApiError } from "@/shared/api/http";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return [error.message, ...error.recommendations].join(" ");
  }
  if (error instanceof Error) return error.message;
  return "Произошла неизвестная ошибка.";
}
