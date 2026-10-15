import WebApp from '@twa-dev/sdk';
import type { Level } from './tutorSettings';
import { grammarRequest } from './grammarRequests';

export const LEARNING_API = ((import.meta.env.VITE_API_BASE as string | undefined) || 'https://api-english.krichigindocs.ru') + '/api/learning';
export interface LearningAnswer { value: string; correct: boolean; correct_answer: string; explanation: string }
export interface LearningQuestion { id: string; prompt: string; choices: string[]; answer?: LearningAnswer; kind?: string; script?: string }
export interface LessonSpec { goal?: string; ordinal: number; cycle: number; position: number; level: string; topic_key: string; title: string; objective: string; kind: string; estimated_minutes: number }
export interface Turn { id: string; text: string; reply: string; feedback: string; score: number | null; assessed: boolean; verified_voice: boolean }
export interface LessonResult { correct: number; total: number; objective: string; words: {word: string; translation: string}[]; speaking_assessed: boolean; checkpoint_due: boolean }
export interface GuidedModule { goal?: string; lessonId: string; level: Level; topicKey: string; conversation?: {title: string; objective: string; prompts: string[]}; onComplete: () => void }
export interface ModuleResult { completed?: boolean; skipped?: boolean; correct?: number; total?: number; turns?: number; history?: {role: string; content: string}[]; podcast?: {session_id: number; transcript: string; audio_url: string; used_words: string[]} }
export interface LearningLesson { modules?: Record<string,ModuleResult>; id: string; status: string; step_index: number; used_seconds: number; activity_token?: string | null; conversation: Turn[]; result: LessonResult | null; listening_text_mode: boolean;
  content: { engine?: string; spec: LessonSpec; warmup: LearningQuestion[]; theory: string; practice: LearningQuestion[]; listening: {script: string; questions: LearningQuestion[]};
    conversation: {objective: string; prompts: string[]; opening: string}; words: {word: string; translation: string}[] } }
export interface SkillMetric { key: string; label: string; attempts: number; accuracy: number | null }
export interface CheckpointResult { cefr: string; level_before: string; after_lessons: number; skills: Record<string,{correct?: number; total?: number; score?: number}>; speaking_feedback?: string }
export interface LearningOverview { enabled: boolean; placement_level?: string | null; goals?: Record<string,string>; profile?: {goal: string; goal_label: string; level: string; completed_count: number; cycle: number; cycle_completed: number; until_checkpoint: number; checkpoint_due: boolean} | null;
  next_lesson?: LearningLesson | null; route?: (LessonSpec & {status: string; id: string | null})[]; skills?: SkillMetric[];
  competencies?: {key: string; label: string; accuracy: number | null; status: string; attempts: number}[];
  history?: {id: string; after_count: number; date: string; result: CheckpointResult}[] }
export interface Checkpoint { id: string; status: string; position: number; total: number; after_count: number; level_before: string; question: LearningQuestion | null; result: CheckpointResult | null }

export function learningUrl(path: string) {
  const url = new URL(LEARNING_API + path);
  if (WebApp.initData) url.searchParams.set('init_data', WebApp.initData);
  return url.toString();
}
export async function learningGet<T>(path: string): Promise<T> {
  const response = await fetch(learningUrl(path));
  if (!response.ok) throw new Error('Не удалось загрузить программу. Проверь соединение и попробуй ещё раз.');
  return response.json() as Promise<T>;
}
export async function learningPost<T>(path: string, body: Record<string,unknown> = {}, keepalive = false): Promise<T> {
  try { return await grammarRequest<T>(LEARNING_API + path, {init_data: WebApp.initData || '', ...body}, {retry: true, keepalive}); }
  catch (error) {
    const detail = error && typeof error === 'object' && 'detail' in error ? String(error.detail) : '';
    const messages: Record<string,string> = {
      lesson_limit_reached: 'На сегодня бесплатный урок уже начат. Его можно продолжить; следующий откроется завтра или с подпиской.',
      account_blocked: 'Доступ к аккаунту ограничен.',
      placement_required: 'Сначала пройди тест уровня, чтобы составить программу.',
      lesson_not_active: 'Урок приостановлен. Нажми «Продолжить», чтобы вернуться к нему.',
      checkpoint_not_due: 'Проверка откроется после двадцати завершённых уроков.',
      answers_required: 'Сначала ответь на задания этого этапа.',
      conversation_required: 'Для завершения разговора нужны три ответа.',
      module_incomplete: 'Сначала заверши текущий этап. Для разговора нужны три ответа и завершение сессии.',
    };
    if (messages[detail]) throw new Error(messages[detail]);
    throw error;
  }
}
export async function learningSetup(goal: string) {
  const response = await fetch(LEARNING_API + '/profile', {method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({goal,init_data:WebApp.initData || ''})});
  if (!response.ok) throw new Error('Не удалось сохранить программу. Проверь соединение и результат теста уровня.');
}
