// GrammarExercise.tsx — компонент одного задания. Только multiple choice:
// текстовый ввод убран из продукта (фидбек владельца). Клик по варианту
// сразу проверяет ответ → feedback и кнопка «Дальше».

export interface Exercise {
  id: string;
  type: string; // всегда "mcq"
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

import { useState } from "react";

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
  const [picked, setPicked] = useState<string>("");
  const [checked, setChecked] = useState<boolean>(false);
  const [isCorrect, setIsCorrect] = useState<boolean>(false);

  const handleChoice = (choice: string) => {
    if (checked) return;
    setPicked(choice);
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

      <div className="grm-choices">
        {exercise.choices.map((choice) => {
          const isPicked = picked === choice;
          const isRight = normalize(choice) === normalize(exercise.correct);
          let state: "idle" | "right" | "wrong" = "idle";
          if (checked) {
            if (isRight) state = "right";
            else if (isPicked) state = "wrong";
          }
          return (
            <button
              key={choice}
              type="button"
              className="grm-choice"
              data-state={state}
              disabled={checked}
              onClick={() => handleChoice(choice)}
            >
              {choice}
            </button>
          );
        })}
      </div>

      {/* Feedback после ответа */}
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
          {exercise.explanation && exercise.explanation !== "—" && (
            <div className="grm-feedback__explanation">{exercise.explanation}</div>
          )}
        </div>
      )}

      {checked && (
        <button type="button" className="grm-primary-btn" onClick={onNext}>
          {isLast ? "Итоги" : "Дальше →"}
        </button>
      )}
    </div>
  );
}
