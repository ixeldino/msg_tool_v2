🌐 [In English](#english-version)


---

# Acronis .MSG Localization Suite

Набір консольних скриптів на Python для розбору, модифікації та збірки бінарних файлів ресурсів (`.msg`), які використовуються в продуктах Acronis (зокрема Acronis True Image).

Утиліти дозволяють конвертувати бінарний файл у текстовий JSON-маніфест, створювати стандартні шаблони перекладу POT/PO для Poedit, збирати змінений файл назад і автоматично знаходити системні константи, які не можна перекладати.

---

## 🔥 Склад проекту

1. **`msg_tool_2.py`** — основний скрипт для роботи з файлами продуктів. Підтримує режими:
* `extract` — розбирає `.msg`, створює маніфест структури та шаблон перекладу `.pot`.
* `build` — збирає новий `.msg` на основі маніфесту та готового файлу перекладу `.po`.
* `validate` — побітово порівнює оригінал та ребілд, локалізує розбіжності до конкретного текстового блоку.
* `inspect` — виводить базову статистику (кількість рядків, блоки, кодування).

> Маніфест являє собою JSON-файл, який містить всю інформацію оригінального файлу `.msg`. В тому числі представлення всіх блоків, розподіл їх структури, визначення порядку їх розміщення, зв'язок з фразами із мовного файлу `.pot/.po`, виключення, тощо. Фактично маніфест містить всю інформацію, що потрібна для повного відтворення початкового файлу `.msg`.

2. **`analyse_ex.py`** — скрипт крос-мовного аналізу. Порівнює файли різних локалізацій і знаходить системні блоки, які не змінились в жодній з проаналізованих локалізацій.

---

## 🔬 Структура файлу .MSG

Бінарний файл складається з чотирьох послідовних частин:

```
┌─────────────────────────────────────────────────────────┐
│ Заголовок (Static Header) — 12 байт                     │
├─────────────────────────────────────────────────────────┤
│ Таблиця зміщень (Offsets Table) — Line Count * 4 байт  │
├─────────────────────────────────────────────────────────┤
│ Розділювач (Separator) — якщо присутній                 │
├─────────────────────────────────────────────────────────┘
▼ Початок пулу даних (Data Pool Start)
┌─────────────────────────────────────────────────────────┐
│ Пул даних (Тексти, табуляції, нуль-термінатори)         │
└─────────────────────────────────────────────────────────┘

```

### Специфікація заголовка (перші 12 байт):

* **Байт 0–3 (`uint32_le`):** `Line Count` — кількість логічних індексів у таблиці зміщень.
* **Байт 4–7 (`uint32_le`):** `Data Pool Size` — розмір текстового пулу в байтах.
* **Байт 8–11 (`uint32_le`):** `System Flags` — системні прапорці.

### Таблиця зміщень:

Починається з **12-го байта**. Містить масив значень `uint32_le` розміром `Line Count`.

> **Важливо:** Усі зміщення в таблиці відраховуються від **початку пулу даних**, а не від початку файлу. Оскільки перший текстовий блок зазвичай лежить на самому початку пулу, його зміщення дорівнює `0`. Через це при перегляді хекс-дампу третій `uint32` заголовка часто плутають із першим офсетом — заголовок завжди фіксований (12 байт), а `0` є першою адресою всередині пулу тексту.

---

## 🧠 Механізм виключень (Exclude List)

У файлах `.msg` крім інтерфейсу користувача зазвичай зашиті системні макроси, конфігурації завантажувача або скрипти (наприклад, блоки з `[start]\ninitrd ramdisk.dat`). Якщо змінити їхню довжину або перекласти, поведінка програми може бути не передбачуваною.

Скрипт `analyse_ex.py` автоматично вирішує цю проблему за таким принципом: якщо бінарний вміст блоку за певним індексом повністю збігається в усіх доступних мовах, цей блок є системним і чіпати його **заборонено**.

Приклад згенерованого файлу `exclude_list.json`:

```json
{
    "metadata": {
        "description": "Блоки, бінарний вміст яких збігається в усіх мовах",
        "total_blocks_analyzed": 2150,
        "languages_analyzed": ["russian", "german", "es", "brazil"]
    },
    "exclude_indices": [42, 115],
    "blocks": {
        "42": {
            "length": 256,
            "raw_hex": "5B73746172745D0A6563686F205374617274696E67...",
            "preview": "[UTF-16] '[start]\necho Starting Acronis...'"
        }
    }
}

```

Під час виконання команди `extract` цей файл автоматично маркує відповідні блоки прапорцем `"translate": false`, і вони не потрапляють до шаблону перекладу.

---

## 🚀 Порядок роботи (на прикладі trueimg_home_pe_en.msg)

### 1. Генерація списку виключень

Зберіть оригінальні мовні файли в одну директорію та запустіть аналізатор:

```bash
python analyse_ex.py --base-dir ./locales --pattern "trueimg_home_pe_{lang}.msg" --languages russian german es brazil

```

### 2. Розбір оригінального файлу

Екстрактуємо дані з англійської версії, захищаючи системні блоки через отриманий раніше `exclude_list.json`:

```bash
python msg_tool_2.py extract trueimg_home_pe_en.msg --manifest trueimg_home_pe_en.manifest --pot trueimg_home_pe_en.pot --exclude exclude_list.json

```

> При розборі скрип автоматично знаходить дублювані фрази і об'єднує їх в один рядок. Формат GNU Gettext не допускає наявності дублікатів і при появі фраз дублів виконує "оптимізацію" `.pot` файла, що призведе до того, що при збірці не буде знайдено потрібний ID рядка і він не буде перекладений. Саме тому кількість фраз на виході буде меншою ніж число блоків у вхідному файлі.

На виході отримаємо карту структури (`.manifest`) та чистий шаблон для перекладу (`.pot`).

### 3. Переклад рядків

Створіть файл перекладу (наприклад, через Poedit або інший інструмент) на основі шаблону та збережіть як `trueimg_home_pe_en-ua.po`. Перекладіть потрібні рядки.

> Для роботи скрипта не потрібен скомпільований файл `.mo`. Його можна видалити. Вся робота ведеться саме з файлом `.po`. Дуже важливим є зберегти ідентифікатори рядків в цьому файлі, оскільки саме за ними відбувається збірка файлу `.msg`. Формат GNU Gettext було використано виключно для зручності перекладу і через наявність великої кількості готових інструментів. 

### 4. Збірка локалізованого файлу

Скомпілюйте новий український `.msg` файл:

```bash
python msg_tool_2.py build --manifest trueimg_home_pe_en.manifest --po trueimg_home_pe_en-ua.po --output trueimg_home_pe_uk.msg

```

> Важливо при збірці використовувати саме той файл маніфесту, з яким створювався файл `.pot`, інакше результатом збірки буде каша з фраз в інтерфейсі програми. 

#### Додаткові параметри збірки:

* `--inject` — **Режим ін'єкції (Byte-for-Byte).** Жорстко фіксує довжину та офсети оригінальних блоків. Якщо новий текст коротший — він дописується пробілами, якщо довший — обрізається з додаванням трикрапки `...`. Мінімізує ризик порушення логіки оригінального бінарника.
* `--align-all 4` — Примусово вирівнює довжину **всіх** блоків у пулі тексту за кордоном 4 байт (заповнює нулями), що може бути необхідно для нормальної роботи в окремих випадках. Доступні значення 1, 2, 4 або 8. За замовчуванням 1 (не вирівнює).
* `--align-masters 4` — вирівнювання ключових блоків до границі в 4 байти. Доступні значення 1, 2, 4 або 8. За замовчуванням 1 (не вирівнює).
* `--force` — зібрати всі блоки на основі даних з файлу перекладу замість маніфесту. Для уникнення колізій, при збірці перекодовуються лише перекладені рядки. Блоки, які не зазнали змін, беруться з маніфесту і повертаються в оригінальному вигляді. При потребі, можна змусити скрипт зібрати все а не брати оригінал. Ця команда потрібна більше для тестів, але, для окремих, малоімовірних сценаріїв, така можливість присутня.

### 5. Валідація результату

Якщо потрібно перевірити коректність збірки на предмет зсуву адрес, запустіть побітове порівняння:

```bash
python msg_tool_2.py validate --original trueimg_home_pe_ru.msg --rebuilt trueimg_home_pe_uk.msg --manifest trueimg_home_pe_ru.manifest

```

У разі розбіжностей скрипт покаже точний байт, Hex-дамп ділянки та ID текстового блоку, в якому сталася помилка.

> Примітка: Якщо виконати команду `extract` і одразу на основі отриманих файлів зібрати новий файл командою `build --force`, то на виході маємо отримати ідентичний оригіналу файл. Команда `validate` має показати ідентичний збіг обох файлів. Проте, на практиці бувають випадки, що через вирівнювання окремих блоків у оригіналі, структура в певний момент міняється. Це не є проблемою - на етапі тестування не виникало проблем з такими аномаліями. Втім, якщо Ви зіткнетесь з проблемою, то можете виконати вирівнювання блоків `--align-masters 4 --align-all 1`, це допоможе вирівняти всі великі блоки до 4 байт, що є частою вимогою для роботи з UTF-16 LE.

---



## 🤖 AI-Assisted Development

Проект розроблено та оптимізовано за активної підтримки інструментів штучного інтелекту (AI). Моделі використовувалися для прискорення реверс-інжинірингу бінарної структури заголовків, написання алгоритмів аналізу контрольних сум, визначення кодувань та реалізації логіки безпечного заповнення байтів у режимі `--inject`.

---

## ⚖️ License & Legal Disclaimer (Ліцензія та відмова від відповідальності)

### UA
Цей проект є **виключно дослідницьким (research project)** та освітнім. Він створений з метою вивчення сумісності, аналізу бінарних структур та автоматизації особистих процесів локалізації. Проект жодним чином не пов'язаний з компанією Acronis, не схвалений нею та не має на меті порушення її авторських прав чи інтелектуальної власності.

* **Відмова від претензій:** Програмне забезпечення надається "як є". Автор не несе жодної відповідальності за потенційне пошкодження завантажувачів, втрату даних, збої в системі або будь-які юридичні наслідки використання цих скриптів. Всі ризики ви берете на себе.
* **Обмеження поширення:** Файли, отримані в результаті роботи скриптів (модифіковані бінарники `.msg`), **заборонено розповсюджувати у публічному доступі**. Вони призначені виключно для власного (приватного) використання.
* **⚠️ Важлива заборона:** Використання цього скрипта, будь-яких його частин, результатів його роботи, ідей, алгоритмів та інформації з цього файлу README громадянами, організаціями або державними структурами російської федерації категорично **заборонено**, попри їхню звичку ігнорувати авторські права та привласнювати чужу працю.

---

<a name="english-version"></a>
# Acronis .MSG Localization Suite (English Version)

A set of Python command-line utilities designed for parsing, modifying, and rebuilding binary resource files (`.msg`) used within Acronis bootable environments (specifically Acronis True Image).

These tools allow you to convert a binary file into a structured JSON manifest, generate standard POT/PO translation templates for Poedit, recompile the modified data back into a valid binary, and automatically detect system constants that must be excluded from translation.

---

## 🔥 Component Overview

1. **`msg_tool_2.py`** — The primary tool for handling target files. It features four operation modes:
   * `extract` — Parses a `.msg` file, generates a structural layout manifest, and outputs a `.pot` translation template.
   * `build` — Recompiles a new `.msg` binary using the manifest and a completed `.po` translation file.
   * `validate` — Performs a deep byte-for-byte comparison between the original and rebuilt binaries, isolating discrepancies down to the specific block ID.
   * `inspect` — Displays baseline metadata (line counts, total physical blocks, detected encodings).
2. **`analyse_ex.py`** — A cross-language analysis script. It compares files across multiple official language distributions to identify system blocks that will break the application if altered or translated.

---

## 🔬 Binary File Structure (.MSG)

The binary file consists of four consecutive sections:


```

┌─────────────────────────────────────────────────────────┐
│ Static Header — 12 bytes                                │
├─────────────────────────────────────────────────────────┤
│ Offsets Table — Line Count * 4 bytes                    │
├─────────────────────────────────────────────────────────┤
│ Separator — if present                                  │
├─────────────────────────────────────────────────────────┘
▼ Data Pool Start
┌─────────────────────────────────────────────────────────┐
│ Data Pool (Strings, tabulations, null-terminators)      │
└─────────────────────────────────────────────────────────┘

```

### Header Specification (First 12 bytes):
* **Bytes 0–3 (`uint32_le`):** `Line Count` — Total number of logical indices in the offsets table.
* **Bytes 4–7 (`uint32_le`):** `Data Pool Size` — The total size of the string pool data in bytes.
* **Bytes 8–11 (`uint32_le`):** `System Flags` — Internal system flags.

### Offsets Table:
Begins precisely at the **12th byte offset**. It holds an array of `uint32_le` elements corresponding to the `Line Count`.

> **Important Note:** All pointers within this table are **relative offsets** calculated from the *beginning of the data pool*, not the file header. Since the first string block typically sits right at the start of the data pool, its offset value is `0`. When inspecting a hex dump, the third `uint32` entry of the header is often mistaken for the first offset pointer. In reality, the header is fixed at 12 bytes, and `0` is simply the first index within the string block pool.

---

## 🧠 Exclude List Mechanism

In addition to user interface strings, `.msg` files embed system macros, bootloader configurations, and scripts (e.g., blocks containing `[start]\ninitrd ramdisk.dat`). Translating these strings or changing their byte lengths will freeze or crash the boot component.

The `analyse_ex.py` script mitigates this risk: if the binary content (hex data) of a block at a specific index is completely identical across English, German, Russian, Spanish, and other language files, it is identified as a critical system constant and **must not be touched**.

Example `exclude_list.json` output:
```json
{
    "metadata": {
        "description": "Blocks with identical binary content across all analyzed languages",
        "total_blocks_analyzed": 2150,
        "languages_analyzed": ["russian", "german", "es", "brazil"]
    },
    "exclude_indices": [42, 115],
    "blocks": {
        "42": {
            "length": 256,
            "raw_hex": "5B73746172745D0A6563686F205374617274696E67...",
            "preview": "[UTF-16] '[start]\necho Starting Acronis...'"
        }
    }
}

```

When running the `extract` command, this JSON file marks matching indices with `"translate": false`, automatically omitting them from the translation template.

---

## 🚀 Step-by-Step Workflow

### 1. Generating the Exclude List

Gather the original official language files into a working directory and run the analyzer:

```bash
python analyse_ex.py --base-dir ./locales --pattern "trueimg_home_pe_{lang}.msg" --languages russian german es brazil

```

### 2. Decompiling the Target File

Extract data from the baseline file (e.g., the Russian version) while protecting system strings via the `exclude_list.json` you generated:

```bash
python msg_tool_2.py extract trueimg_home_pe_ru.msg --manifest trueimg_home_pe_ru.manifest --pot trueimg_home_pe_ru.pot --exclude exclude_list.json

```

This generates a structural blueprint (`.manifest`) and a clean template file (`.pot`) for localization.

### 3. Translating Strings

Initialize a localization file (e.g., using Poedit) from your `.pot` template, save it as `trueimg_home_pe_ru-ua.po`, and translate the text into your target language.

### 4. Rebuilding the Localized Binary

Compile your new language file into the target `.msg` format:

```bash
python msg_tool_2.py build --manifest trueimg_home_pe_ru.manifest --po trueimg_home_pe_ru-ua.po --output trueimg_home_pe_uk.msg

```

#### Advanced Compilation Arguments:

* `--inject` — **In-place Injection Mode (Byte-for-Byte).** Locks the original offsets and block lengths. If a translated string is shorter than the original, it is padded with spaces; if longer, it is truncated safely with an appended ellipsis `...`. This minimizes any chance of breaking internal binary pointers.
* `--align-all 4` — Forces all re-encoded block lengths to align to a 4-byte boundary (padded with zeros), which is required by certain legacy Acronis bootloader versions.

### 5. Validating the Output

To verify layout integrity and ensure no addressing shifts occurred, run the differential validation check:

```bash
python msg_tool_2.py validate --original trueimg_home_pe_ru.msg --rebuilt trueimg_home_pe_uk.msg --manifest trueimg_home_pe_ru.manifest

```

If errors are found, the script flags the exact byte offset, outputs a hex dump snippet, and matches it to the source block ID.

---

## ⚙️ Automation Script Example (`build_all.bat`)

To streamline repetitive building steps, you can use this simple batch script:

```batch
@echo off
echo [*] Recompiling localization resource file...

:: 1. Refresh manifest layout and POT template
python msg_tool_2.py extract trueimg_home_pe_ru.msg --manifest trueimg_home_pe_ru.manifest --pot trueimg_home_pe_ru.pot --exclude exclude_list.json

:: 2. Rebuild the final binary with 1-byte alignment
python msg_tool_2.py build --manifest trueimg_home_pe_ru.manifest --po trueimg_home_pe_ru-ua.po --output trueimg_home_pe_uk.msg --align-all 1

:: 3. Fast metadata check of the compiled output
python msg_tool_2.py inspect trueimg_home_pe_uk.msg

echo [+] Process completed successfully.
pause

```

---

## 🤖 AI-Assisted Development

This suite was built and refined with the active assistance of Artificial Intelligence (AI) tools. Large language models were leveraged to accelerate the reverse-engineering of proprietary `.msg` file headers, write character encoding fallback handlers, and implement data padding routines for the `--inject` engine.

## ⚖️ License & Legal Disclaimer (Ліцензія та відмова від відповідальності)
### EN
This project is strictly for **educational and research purposes**. It was built to study binary layouts, interoperability, and personal localization automation. It is not affiliated with or endorsed by Acronis.

* **Disclaimer:** The software is provided "as is". The author bears zero liability for any system instability, bootloader corruption, data loss, or legal actions resulting from the execution of these scripts. You use it at your own risk.
* **Distribution Limit:** Compiled output files (`.msg`) resulting from this toolset **must not be shared publicly**. They are restricted to private, individual use only.
* **⚠️ Crucial Restriction:** Any utilization, reproduction, or modification of this code, documentation, or conceptual layout by individuals, entities, or state bodies associated with the Russian Federation (⚪🔵⚪) is **strictly prohibited**.
