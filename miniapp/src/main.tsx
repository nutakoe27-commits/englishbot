import React from "react";
import ReactDOM from "react-dom/client";
import WebApp from "@twa-dev/sdk";
import App from "./App";
import { BattleScreen } from "./BattleScreen";

// Диспетчер: читаем Telegram startapp payload. Если это «battle_<id>_<side>» —
// показываем экран баттла. Иначе — обычный голосовой режим.
function Root() {
  const rawParam = (WebApp.initDataUnsafe?.start_param as string | undefined) || "";
  // Telegram в some cases кладёт payload в hash вроде #tgWebAppStartParam=battle_12_a
  // @twa-dev/sdk парсит это сам, но оставим фолбэк на window.location для dev.
  let startParam = rawParam;
  if (!startParam && typeof window !== "undefined") {
    const m = window.location.hash.match(/tgWebAppStartParam=([^&]+)/);
    if (m) startParam = decodeURIComponent(m[1]);
  }

  if (startParam.startsWith("battle_")) {
    // формат: battle_<id>_<side>
    const parts = startParam.split("_");
    const id = parseInt(parts[1] || "0", 10);
    const side = (parts[2] === "b" ? "b" : "a") as "a" | "b";
    if (id > 0) {
      return <BattleScreen battleId={id} side={side} />;
    }
  }

  return <App />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
