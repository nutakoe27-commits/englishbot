import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LearningScreen } from '../src/LearningScreen';
import '../src/ds/styles.css';

function Harness() {
  const [visible,setVisible]=useState(true);
  return <><button onClick={() => setVisible(false)}>Unmount learning</button>
    {visible ? <LearningScreen onExit={() => setVisible(false)} onPlacement={() => setVisible(false)} /> : <p>Closed</p>}</>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><Harness /></StrictMode>);
