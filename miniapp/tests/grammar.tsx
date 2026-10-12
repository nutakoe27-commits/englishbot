// Isolated real React screen for network failure/cancellation browser tests.
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { GrammarScreen } from "../src/GrammarScreen";

function Harness() {
  const [visible, setVisible] = useState(true);
  return <>
    <button onClick={() => setVisible(false)}>Unmount grammar</button>
    {visible && <GrammarScreen onExit={() => setVisible(false)} />}
  </>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Harness /></StrictMode>);
