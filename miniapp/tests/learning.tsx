import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LearningScreen } from '../src/LearningScreen';
import '../src/ds/styles.css';
import '../src/ds-react/components.css';
import { installFetchAuth } from '../src/auth';
installFetchAuth();

function Harness() {
  const [visible,setVisible]=useState(true);
  return <div className="app-shell"><div className="app-shell__body">
    {visible ? <LearningScreen onExit={() => setVisible(false)} onPlacement={() => setVisible(false)} /> : <p>Closed</p>}</div></div>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><Harness /></StrictMode>);
