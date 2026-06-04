// Transcript.tsx — раскрываемая панель с текстом подкаста. Tap на слове
// открывает TranslatePopover (тот же, что в speaking-режиме).

import { useState } from "react";
import WebApp from "@twa-dev/sdk";
import { TranslatePopover } from "./TranslatePopover";

interface Props {
  apiBase: string;
  text: string;
  initiallyVisible?: boolean;
}

interface TranslateTarget {
  word: string;
  context: string;
  x: number;
  y: number;
}

// Контекст для перевода — окружающее предложение (по точке/!/?).
function findSentence(text: string, wordIndex: number): string {
  const before = text.slice(0, wordIndex);
  const after = text.slice(wordIndex);
  const startMatch = before.match(/[.!?]\s+[^.!?]*$/);
  const start = startMatch ? wordIndex - (startMatch[0].length - startMatch[0].search(/\S/)) : 0;
  const endMatch = after.match(/[^.!?]*[.!?]/);
  const end = endMatch ? wordIndex + endMatch[0].length : text.length;
  return text.slice(start, end).trim();
}

export function Transcript({ apiBase, text, initiallyVisible = false }: Props) {
  const [visible, setVisible] = useState<boolean>(initiallyVisible);
  const [target, setTarget] = useState<TranslateTarget | null>(null);

  // Разбиваем на чередующиеся токены [слово, разделитель, слово, ...].
  // Считаем позицию каждого слова в исходной строке, чтобы вытащить контекст.
  const tokens: { text: string; isWord: boolean; index: number }[] = [];
  {
    let cursor = 0;
    const re = /(\w[\w'-]*)|([^\w]+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      if (m[1]) {
        tokens.push({ text: m[1], isWord: true, index: cursor });
      } else if (m[2]) {
        tokens.push({ text: m[2], isWord: false, index: cursor });
      }
      cursor += m[0].length;
    }
  }

  const handleWordTap = (
    e: React.MouseEvent<HTMLSpanElement>,
    word: string,
    wordIndex: number,
  ) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTarget({
      word,
      context: findSentence(text, wordIndex),
      x: rect.left,
      y: rect.bottom + 6,
    });
  };

  return (
    <div className="lst-transcript">
      <button
        type="button"
        className="lst-secondary-btn lst-transcript__toggle"
        onClick={() => setVisible((v) => !v)}
      >
        {visible ? "Скрыть текст" : "Показать текст"}
      </button>
      {visible && (
        <div className="lst-transcript__body">
          {tokens.map((tok, i) =>
            tok.isWord ? (
              <span
                key={i}
                className="lst-transcript__word"
                onClick={(e) => handleWordTap(e, tok.text, tok.index)}
              >
                {tok.text}
              </span>
            ) : (
              <span key={i}>{tok.text}</span>
            ),
          )}
        </div>
      )}
      {target && (
        <TranslatePopover
          apiBase={apiBase}
          initData={WebApp.initData || ""}
          word={target.word}
          context={target.context}
          x={target.x}
          y={target.y}
          onClose={() => setTarget(null)}
        />
      )}
    </div>
  );
}
