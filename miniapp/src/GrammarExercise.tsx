// GrammarExercise.tsx — компонент одного задания.
// Controlled: задание + локальное состояние (выбранный вариант / ввод)
// → onAnswer(userAnswer, isCorrect) когда юзер жмёт «Проверить».
// После проверки — feedback и кнопка «Дальше».

import { useState } from "react";

export interface Exercise {
  id: string;
  type: "mcq" | "fill";
  category: string;
  prompt: string;
  choices: string[];
  correct: string;
  explanation: string;
}

interface Props {
  exercise: Exercise;
  index: number;
  total: number;
  onAnswer: (userAnswer: string, isCorrect: boolean) => void;
  onNext: () => void;
  isLast: boolean;
}

// Нормализация для сравнения ответа: trim, lowercase, схлопывание пробелов,
// убираем хвостовые знаки препинания. Так «went.» == «Went» == «  went ».
function normalize(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[.!?,;:]+$/g, "");
}

export function GrammarExercise({
  exercise,
  index,
  total,
  onAnswer,
  onNext,
  isLast,
}: Props) {
  const [userAnswer, setUserAnswer] = useState<string>("");
  const [checked, setChecked] = useState<boolean>(false);
  const [isCorrect, setIsCorrect] = useState<boolean>(false);

  const handleCheck = () => {
    if (checked) return;
    const trimmed = userAnswer.trim();
    if (!trimmed) return;
    const ok = normalize(trimmed) === normalize(exercise.correct);
    setIsCorrect(ok);
    setChecked(true);
    onAnswer(trimmed, ok);
  };

  const handleMcqClick = (choice: string) => {
    if (checked) return;
    setUserAnswer(choice);
    const ok = normalize(choice) === normalize(exercise.correct);
    setIsCorrect(ok);
    setChecked(true);
    onAnswer(choice, ok);
  };

  const progressPct = Math.round(((index + 1) / total) * 100);

  return (
    <div className="grm-exercise">
      <div className="grm-progress">
        <div className="grm-progress__bar">
          <div
            className="grm-progress__fill"
            style={{ width: `${progressPct}%` }}
            aria-hidden
          />
        </div>
        <span className="grm-progress__label">
          {index + 1} / {total}
        </span>
      </div>

      <p className="grm-prompt">{exercise.prompt}</p>

      {exercise.type === "mcq" ? (
        <div className="grm-choices">
          {exercise.choices.map((choice) => {
            const isPicked = userAnswer === choice;
            const isRight = normalize(choice) === normalize(exercise.correct);
            let state: "idle" | "picked" | "right" | "wrong" = "idle";
            if (checked) {
              if (isRight) state = "right";
              else if (isPicked) state = "wrong";
            } else if (isPicked) {
              state = "picked";
            }
            return (
              <button
                key={choice}
                type="button"
                className="grm-choice"
                data-state={state}
                disabled={checked}
                onClick={() => handleMcqClick(choice)}
              >
                {choice}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="grm-fill">
          <input
            type="text"
            inputMode="text"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={userAnswer}
            onChange={(e) => setUserAnswer(e.target.value)}
            disabled={checked}
            placeholder="Введи ответ"
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCheck();
            }}
            aria-label="Ответ"
          />
        </div>
      )}

      {/* Feedback после проверки */}
      {checked && (
        <div
          className={
            "grm-feedback " +
            (isCorrect ? "grm-feedback--correct" : "grm-feedback--wrong")
          }
          role="status"
        >
          <div className="grm-feedback__head">
            {isCorrect ? "✅ Верно" : `❌ Правильный ответ: ${exercise.correct}`}
          </div>
          {!isCorrect && exercise.type === "fill" && (
            <div className="grm-feedback__your">
              Ты ввёл: <em>{userAnswer || "—"}</em>
            </div>
          )}
          {exercise.explanation && exercise.explanation !== "—" && (
            <div className="grm-feedback__explanation">{exercise.explanation}</div>
          )}
        </div>
      )}

      {/* Кнопка действия */}
      {!checked ? (
        <button
          type="button"
          className="grm-primary-btn"
          onClick={handleCheck}
          disabled={
            exercise.type === "fill" ? !userAnswer.trim() : !userAnswer
          }
        >
          Проверить
        </button>
      ) : (
        <button type="button" className="grm-primary-btn" onClick={onNext}>
          {isLast ? "Итоги" : "Дальше →"}
        </button>
      )}
    </div>
  );
}
