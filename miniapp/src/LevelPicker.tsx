import { useEffect, useState } from 'react';
import WebApp from '@twa-dev/sdk';
import { API_BASE } from './auth';
import { LEVEL_OPTIONS, loadSettings, saveSettings, type Level } from './tutorSettings';

export function LevelPicker({value,onSaved}: {value: string; onSaved: (level: Level) => void | Promise<void>}) {
  const [level,setLevel]=useState(value || 'A1');
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [saved,setSaved]=useState(false);
  useEffect(() => {setLevel(value || 'A1');},[value]);
  const save=async () => {
    if (busy) return;
    setBusy(true); setError(''); setSaved(false);
    try {
      const response=await fetch(API_BASE+'/api/auth/level',{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({level,init_data:WebApp.initData || ''})});
      if (!response.ok) throw new Error('Не удалось изменить уровень. Попробуй ещё раз.');
      saveSettings({...loadSettings(),level:level as Level});
      await onSaved(level as Level); setSaved(true);
    } catch (error) {setError(error instanceof Error ? error.message : 'Не удалось сохранить уровень.');}
    finally {setBusy(false);}
  };
  return <div className="level-picker">
    <label>Уровень занятий<select value={level} disabled={busy} onChange={event => {setLevel(event.target.value); setSaved(false);}}>
      {LEVEL_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label} — {option.hint}</option>)}
    </select></label>
    <p>Выбери комфортную сложность. Новые занятия подстроятся под неё, а начатый урок и история проверок сохранятся.</p>
    <button type="button" className="learn-secondary" disabled={busy || level===value} onClick={() => void save()}>{busy ? 'Сохраняем…' : 'Изменить уровень'}</button>
    {saved && <p role="status">Уровень сохранён</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
