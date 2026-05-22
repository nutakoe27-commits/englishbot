// Простой word-level diff через LCS. Используется для подсветки разницы
// между ошибочной фразой юзера и correction'ом тьютора.

export type DiffOp = { kind: "eq" | "del" | "ins"; text: string };

/** Разбивает строку на токены: слова И не-слова (пробелы/пунктуация),
 *  чтобы рендер сохранил исходное форматирование. */
function tokenize(s: string): string[] {
  return s.match(/\w+|\W+/g) || [];
}

/** Сравнивает два токена case-insensitive — типичный case "I" vs "i" не
 *  считается отличием для целей correction-diff'а. */
function eq(a: string, b: string): boolean {
  return a.localeCompare(b, undefined, { sensitivity: "accent" }) === 0;
}

/** LCS-based word diff. На вход — оригинал и correction. На выход —
 *  плоский массив операций (порядок исходных + добавленных токенов). */
export function wordDiff(original: string, corrected: string): DiffOp[] {
  const a = tokenize(original);
  const b = tokenize(corrected);

  // dp[i][j] = длина LCS для префиксов a[..i], b[..j].
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array(m + 1).fill(0),
  );
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (eq(a[i - 1], b[j - 1])) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Trace-back с конца. Собираем reversed-list операций.
  const ops: DiffOp[] = [];
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (eq(a[i - 1], b[j - 1])) {
      ops.push({ kind: "eq", text: b[j - 1] });
      i--;
      j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      ops.push({ kind: "del", text: a[i - 1] });
      i--;
    } else {
      ops.push({ kind: "ins", text: b[j - 1] });
      j--;
    }
  }
  while (i > 0) {
    ops.push({ kind: "del", text: a[i - 1] });
    i--;
  }
  while (j > 0) {
    ops.push({ kind: "ins", text: b[j - 1] });
    j--;
  }
  ops.reverse();

  // Склеиваем соседние операции одного типа — уменьшаем мусор в DOM.
  const merged: DiffOp[] = [];
  for (const op of ops) {
    const last = merged[merged.length - 1];
    if (last && last.kind === op.kind) {
      last.text += op.text;
    } else {
      merged.push({ ...op });
    }
  }
  return merged;
}

/** Проверяет, что diff "осмысленный" (есть точки сходства). Если correction
 *  полностью переписан (LCS почти 0) — лучше показать плоский текст. */
export function diffLooksMeaningful(ops: DiffOp[]): boolean {
  const totalChars = ops.reduce((s, op) => s + op.text.length, 0);
  if (totalChars === 0) return false;
  const eqChars = ops
    .filter((op) => op.kind === "eq")
    .reduce((s, op) => s + op.text.length, 0);
  // Если общего >= 30% — diff информативный.
  return eqChars / totalChars >= 0.3;
}
