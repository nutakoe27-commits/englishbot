export interface GrammarTicket { version: number; signal: AbortSignal }

export class GrammarRound {
  private version = 0;
  private controller: AbortController | null = null;
  begin(): GrammarTicket {
    this.cancel();
    this.controller = new AbortController();
    return { version: this.version, signal: this.controller.signal };
  }
  current(): GrammarTicket | null {
    return this.controller ? { version: this.version, signal: this.controller.signal } : null;
  }
  owns(ticket: GrammarTicket): boolean {
    return ticket.version === this.version && !ticket.signal.aborted;
  }
  cancel(): void {
    this.controller?.abort();
    this.controller = null;
    this.version++;
  }
}

export class GrammarRequestError extends Error {
  constructor(public readonly status: number, public readonly detail: string, message: string) { super(message); }
}

const messages: Record<string, string> = {
  generation_timeout: "Генерация заняла слишком много времени. Попробуй ещё раз.",
  llm_unavailable: "Не удалось связаться с преподавателем. Попробуй ещё раз.",
  too_few_exercises: "Не получилось подготовить достаточно заданий. Попробуй ещё раз.",
  grammar_preparation_expired: "Задания устарели после перезапуска или долгого ожидания. Подготовь их заново.",
  grammar_session_abandoned: "Занятие было прервано. Подготовь задания заново.",
};

export async function grammarRequest<T>(url: string, body: unknown, options: {
  signal?: AbortSignal; retry?: boolean; keepalive?: boolean;
} = {}): Promise<T> {
  const serialized = JSON.stringify(body);
  const attempts = options.retry ? 3 : 1;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
        body: serialized, signal: options.signal, keepalive: options.keepalive });
      if (!response.ok) {
        const data = await response.json().catch(() => ({})) as { detail?: unknown };
        const detail = typeof data.detail === "string" ? data.detail : "";
        throw new GrammarRequestError(response.status, detail,
          messages[detail] || (response.status >= 500 ? "Сервис временно недоступен. Попробуй ещё раз." : "Не удалось выполнить запрос. Обнови страницу и попробуй ещё раз."));
      }
      return await response.json() as T;
    } catch (error) {
      if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      const retryable = !(error instanceof GrammarRequestError) || error.status >= 500;
      if (!retryable || attempt + 1 === attempts) {
        if (error instanceof GrammarRequestError) throw error;
        throw new Error("Соединение прервалось. Проверь интернет и попробуй ещё раз.");
      }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  throw new Error("Не удалось выполнить запрос");
}
