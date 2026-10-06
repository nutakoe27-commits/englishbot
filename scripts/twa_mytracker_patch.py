#!/usr/bin/env python3
"""
twa_mytracker_patch.py — встроить SDK VK MyTracker в Android-проект Bubblewrap.

Что делает (идемпотентно, можно запускать повторно):
  1. app/build.gradle: зависимость com.my.tracker:mytracker-sdk.
  2. Application: MyTracker.initTracker(<SDK-ключ>) в onCreate. Если в проекте
     уже есть Application.java от Bubblewrap — дописывает туда, иначе создаёт
     MtApplication.java и прописывает его в AndroidManifest.xml.
  3. LauncherActivity.getLaunchingUrl(): добавляет к стартовому адресу
     ?mt_iid=<instanceId>. Сайт отдаёт его бэкенду после входа, а бэкенд шлёт
     регистрацию и оплату в MyTracker с этим instanceId (docs/mytracker.md).

Запуск на Mac из папки, где лежит twa-manifest.json:
    python3 /path/to/englishbot/scripts/twa_mytracker_patch.py . <SDK_KEY>

Порядок при каждой новой версии: поднять appVersionCode в twa-manifest.json →
`bubblewrap update` (перегенерирует проект и стирает правки) → этот скрипт →
`bubblewrap build`.
Перед изменением каждого файла кладёт рядом копию *.bak.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

SDK_DEP = "implementation 'com.my.tracker:mytracker-sdk:3.+'"
MARK = "// MyTracker (scripts/twa_mytracker_patch.py)"


def die(msg: str) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(1)


def backup_write(path: Path, text: str) -> None:
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(text, encoding="utf-8")


def find_package(root: Path) -> str:
    tm = root / "twa-manifest.json"
    if tm.exists():
        pkg = json.loads(tm.read_text(encoding="utf-8")).get("packageId")
        if pkg:
            return pkg
    gradle = (root / "app" / "build.gradle").read_text(encoding="utf-8")
    m = re.search(r'applicationId\s+"([^"]+)"', gradle)
    if not m:
        die("не нашёл packageId ни в twa-manifest.json, ни в app/build.gradle")
    return m.group(1)


def java_dir(root: Path, pkg: str) -> Path:
    d = root / "app" / "src" / "main" / "java" / Path(*pkg.split("."))
    if not d.is_dir():
        # Bubblewrap кладёт исходники в папку пакета; ищем LauncherActivity.
        hits = list((root / "app" / "src" / "main" / "java").rglob("LauncherActivity.java"))
        if not hits:
            die(f"не нашёл исходники приложения (ожидал {d})")
        d = hits[0].parent
    return d


def patch_gradle(root: Path) -> None:
    p = root / "app" / "build.gradle"
    s = p.read_text(encoding="utf-8")
    if "com.my.tracker:mytracker-sdk" in s:
        print("  build.gradle: SDK уже подключён")
        return
    m = re.search(r"dependencies\s*\{", s)
    if not m:
        die("в app/build.gradle нет блока dependencies { }")
    s = s[: m.end()] + f"\n    {MARK}\n    {SDK_DEP}" + s[m.end():]
    backup_write(p, s)
    print("  build.gradle: добавлен", SDK_DEP)

    # Артефакты MyTracker лежат в Maven Central — проверим, что он подключён.
    for name in ("settings.gradle", "build.gradle"):
        f = root / name
        if f.exists() and "mavenCentral()" in f.read_text(encoding="utf-8"):
            return
    print("  ВНИМАНИЕ: не нашёл mavenCentral() в settings.gradle/build.gradle — "
          "добавь его в repositories, иначе зависимость не скачается")


INIT_LINE = 'com.my.tracker.MyTracker.initTracker("{key}", this);'


def patch_application(root: Path, src: Path, pkg: str, key: str) -> None:
    manifest = root / "app" / "src" / "main" / "AndroidManifest.xml"
    ms = manifest.read_text(encoding="utf-8")
    app_java = src / "Application.java"

    if app_java.exists():
        s = app_java.read_text(encoding="utf-8")
        if "MyTracker.initTracker" in s:
            s = re.sub(r'MyTracker\.initTracker\("[^"]*"', f'MyTracker.initTracker("{key}"', s)
            backup_write(app_java, s)
            print("  Application.java: инициализация уже есть, ключ обновлён")
            return
        m = re.search(r"super\.onCreate\(\);", s)
        if not m:
            die("в Application.java нет super.onCreate(); — проверь файл вручную")
        s = s[: m.end()] + f"\n        {MARK}\n        " + INIT_LINE.format(key=key) + s[m.end():]
        backup_write(app_java, s)
        print("  Application.java: добавлена инициализация MyTracker")
        return

    mt_java = src / "MtApplication.java"
    mt_java.write_text(f"""package {pkg};

{MARK}
// Инициализация SDK: MyTracker сам считает установки и запуски приложения.
public class MtApplication extends android.app.Application {{
    @Override
    public void onCreate() {{
        super.onCreate();
        {INIT_LINE.format(key=key)}
    }}
}}
""", encoding="utf-8")
    print("  MtApplication.java: создан")

    m = re.search(r"<application\b[^>]*>", ms, re.S)
    if not m:
        die("в AndroidManifest.xml нет тега <application>")
    tag = m.group(0)
    if "android:name=" in tag:
        if ".MtApplication" in tag:
            print("  AndroidManifest.xml: MtApplication уже прописан")
            return
        die("у <application> уже есть android:name — свой Application-класс. "
            "Добавь в его onCreate строку: " + INIT_LINE.format(key=key))
    new_tag = tag.replace("<application", '<application\n        android:name=".MtApplication"', 1)
    backup_write(manifest, ms.replace(tag, new_tag, 1))
    print("  AndroidManifest.xml: android:name=\".MtApplication\"")


LAUNCH_HELPER = f"""
    {MARK}
    // Идентификатор установки MyTracker → в стартовый адрес (?mt_iid=…).
    // getInstanceId не зовём в главном потоке: ждём не дольше 700 мс, иначе
    // запускаемся без параметра (сайт возьмёт его при следующем запуске).
    private Uri withMyTrackerInstance(Uri uri) {{
        if (uri == null) return null;
        try {{
            final android.content.Context ctx = getApplicationContext();
            java.util.concurrent.ExecutorService ex =
                    java.util.concurrent.Executors.newSingleThreadExecutor();
            java.util.concurrent.Future<String> f =
                    ex.submit(new java.util.concurrent.Callable<String>() {{
                        @Override
                        public String call() {{
                            return com.my.tracker.MyTracker.getInstanceId(ctx);
                        }}
                    }});
            String iid = f.get(700, java.util.concurrent.TimeUnit.MILLISECONDS);
            ex.shutdown();
            if (iid != null && !iid.isEmpty() && uri.getQueryParameter("mt_iid") == null) {{
                return uri.buildUpon().appendQueryParameter("mt_iid", iid).build();
            }}
        }} catch (Exception ignored) {{ }}
        return uri;
    }}
"""


def patch_launcher(src: Path) -> None:
    p = src / "LauncherActivity.java"
    if not p.exists():
        die(f"нет {p}")
    s = p.read_text(encoding="utf-8")
    if "withMyTrackerInstance" in s:
        print("  LauncherActivity.java: уже пропатчен")
        return
    if "import android.net.Uri;" not in s:
        s = re.sub(r"(package [^;]+;\n)", r"\1\nimport android.net.Uri;\n", s, count=1)

    m = re.search(r"protected\s+Uri\s+getLaunchingUrl\s*\(\s*\)\s*\{", s)
    if m:
        # В шаблоне Bubblewrap метод заканчивается на `return uri;`.
        body_start = m.end()
        ret = re.search(r"return\s+uri\s*;", s[body_start:])
        if not ret:
            die("в getLaunchingUrl() не нашёл `return uri;` — пропатчь вручную")
        a, b = body_start + ret.start(), body_start + ret.end()
        s = s[:a] + "return withMyTrackerInstance(uri);" + s[b:]
    else:
        override = """
    @Override
    protected Uri getLaunchingUrl() {
        return withMyTrackerInstance(super.getLaunchingUrl());
    }
"""
        last = s.rstrip().rfind("}")
        s = s[:last] + override + s[last:]

    last = s.rstrip().rfind("}")
    s = s[:last] + LAUNCH_HELPER + s[last:]
    backup_write(p, s)
    print("  LauncherActivity.java: mt_iid добавляется к стартовому адресу")


def main() -> None:
    if len(sys.argv) != 3:
        die("использование: python3 twa_mytracker_patch.py <папка проекта> <SDK_KEY>")
    root = Path(sys.argv[1]).resolve()
    key = sys.argv[2].strip()
    if not re.fullmatch(r"[0-9A-Za-z]{8,64}", key):
        die("SDK-ключ выглядит странно: ожидаю 8–64 латинских букв и цифр")
    if not (root / "app" / "build.gradle").exists():
        die(f"{root} не похож на проект Bubblewrap (нет app/build.gradle)")
    pkg = find_package(root)
    src = java_dir(root, pkg)
    print(f"Проект: {root}\nПакет: {pkg}\nИсходники: {src}")
    patch_gradle(root)
    patch_application(root, src, pkg, key)
    patch_launcher(src)
    print("\nГотово. Собирай: bubblewrap build\n"
          "Порядок при новой версии: поднять appVersionCode в twa-manifest.json → "
          "bubblewrap update → этот скрипт → bubblewrap build. "
          "update перегенерирует проект и стирает правки, поэтому скрипт — после него.")


if __name__ == "__main__":
    main()
