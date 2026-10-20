import { useRef, useState } from "react";
import type { DraftResult } from "../model/draft";

interface Props {
  onSave: () => DraftResult;
  onLoad: () => DraftResult;
}

export function DraftControls({ onSave, onLoad }: Props) {
  const [feedback, setFeedback] = useState<(DraftResult & { action: string; time: string }) | null>(null);
  const message = useRef<HTMLParagraphElement>(null);
  function perform(action: "save" | "load") {
    const result = action === "save" ? onSave() : onLoad();
    setFeedback({ ...result, action, time: new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) });
    requestAnimationFrame(() => {
      if (action === "load" && result.success) {
        const panel = document.getElementById("mission-inputs");
        panel?.scrollTo({ top: 0, behavior: "instant" });
        panel?.focus({ preventScroll: true });
      } else message.current?.focus({ preventScroll: true });
    });
  }
  return <section className="draft-controls" aria-label="Черновик в браузере">
    <b>Черновик в браузере</b>
    <p className="hint">Одно сохранение настроек задания. «Сохранить» заменяет предыдущий черновик, «Открыть» возвращает сохранённые настройки вместо текущих. Результат расчёта в черновик не входит.</p>
    <div className="mini-actions scenario-actions">
      <button type="button" onClick={() => perform("save")}>Сохранить черновик</button>
      <button type="button" onClick={() => perform("load")}>Открыть черновик</button>
    </div>
    {feedback && <p ref={message} tabIndex={-1} aria-live="polite" className={`draft-feedback ${feedback.success ? "success" : "error"}`}>
      {feedback.message} <small>{feedback.time}</small>
    </p>}
    {feedback?.success && feedback.action === "load" && <div className="draft-toast" aria-live="polite">Черновик открыт. Настройки восстановлены - проверьте задание перед расчётом.<button type="button" aria-label="Закрыть уведомление о черновике" onClick={() => setFeedback(null)}>×</button></div>}
    <p className="hint">Для сохранения плана, исходных файлов и истории используйте проекты ниже.</p>
  </section>;
}
