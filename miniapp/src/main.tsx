import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import WebApp from "@twa-dev/sdk";
import App from "./App";
import { BattleScreen } from "./BattleScreen";
import { ModeSelector, type Mode } from "./ModeSelector";
import { ListeningScreen } from "./ListeningScreen";

// Диспетчер: читаем Telegram startapp payload. Если это «battle_<id>_<side>» —
// показываем экран баттла. Иначе — ModeSelector с выбором между speaking и listening.
function Root() {
  const rawParam = (WebApp.initDataUnsafe?.start_param as string | undefined) || "";
  let startParam = rawParam;
  if (!startParam && typeof window !== "undefined") {
    const m = window.location.hash.match(/tgWebAppStartParam=([^&]+)/);
    if (m) startParam = decodeURIComponent(m[1]);
  }

  if (startParam.startsWith("battle_")) {
    const parts = startParam.split("_");
    const id = parseInt(parts[1] || "0", 10);
    const side = (parts[2] === "b" ? "b" : "a") as "a" | "b";
    if (id > 0) {
      return <BattleScreen battleId={id} side={side} />;
    }
  }

  const [screen, setScreen] = useState<Mode | "selector">("selector");
  const backToSelector = () => setScreen("selector");

  if (screen === "speaking") return <App onExit={backToSelector} />;
  if (screen === "listening") return <ListeningScreen onExit={backToSelector} />;
  return <ModeSelector onPick={setScreen} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
