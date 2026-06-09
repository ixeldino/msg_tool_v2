import argparse
import json
import os
import struct
import sys
from pathlib import Path

# Дефолтний список мов, який використовуватиметься, якщо користувач не вказав свій
DEFAULT_LANGS = [
    "brazil", "chineses", "chineset", "czech", "dutch", "es", "german",
    "id", "italian", "japan", "korean", "polish", "portuguese", "russian",
    "spanish", "thefrench"
]


def parse_msg_by_unique_offsets(file_path: Path):
    """
    Парсить файл .msg на основі унікальних відсортованих офсетів.
    """
    if not file_path.exists():
        print(f"[Попередження] Файл не знайдено: {file_path}")
        return None
        
    data = file_path.read_bytes()
    if len(data) < 12:
        print(f"[Помилка] Файл занадто малий або пошкоджений: {file_path}")
        return None
        
    line_count = struct.unpack_from("<I", data, 0)[0]
    data_pool_size = struct.unpack_from("<I", data, 4)[0]
    
    raw_data_offset = len(data) - data_pool_size
    raw_data = data[raw_data_offset:]
    
    raw_offsets = []
    for i in range(line_count):
        off = struct.unpack_from("<I", data, 12 + i * 4)[0]
        raw_offsets.append(off)
        
    valid_offsets = sorted(list(set([o for o in raw_offsets if o < data_pool_size])))
    
    blocks = []
    for i in range(len(valid_offsets)):
        offset = valid_offsets[i]
        next_start = valid_offsets[i+1] if i + 1 < len(valid_offsets) else data_pool_size
        
        block_bytes = raw_data[offset:next_start]
        
        blocks.append({
            "offset": offset,
            "length": len(block_bytes),
            "raw_hex": block_bytes.hex().upper()
        })
        
    return blocks


def generate_preview(hex_str: str) -> str:
    """Генерує текстове прев'ю для бінарного блоку (для чистоти звіту)"""
    if not hex_str:
        return "[Empty]"
    try:
        raw_bytes = bytes.fromhex(hex_str)
        # Спроба декодувати як UTF-16LE або CP1251 для наочності в JSON
        if len(raw_bytes) >= 2:
            try:
                txt = raw_bytes.decode("utf-16-le", errors="strict").strip("\x00")
                if any(c.isalnum() for c in txt):
                    return f"[UTF-16] '{txt}'"
            except:
                pass
        txt = raw_bytes.decode("cp1251", errors="ignore").strip("\x00")
        return f"[CP1251] '{txt}'" if txt else f"[HEX] {hex_str[:10]}..."
    except:
        return f"[HEX] {hex_str[:10]}..."


def analyze_exclusions(base_path: Path, languages: list, pattern: str, output_file: Path):
    """
    Основна логіка крос-мовного аналізу та формування маніфесту виключень.
    """
    lang_data = {}
    
    print(f"[*] Сканування директорій у: {base_path.resolve()}")
    for lang in languages:
        # Підставляємо назву мови в шаблон імені файлу
        try:
            file_name = pattern.format(lang=lang)
        except KeyError:
            print(f"[Критична помилка] Невірний формат шаблону. Використовуйте '{{lang}}' у назві файлу.")
            sys.exit(1)
            
        file_path = base_path / lang / file_name
        
        blocks = parse_msg_by_unique_offsets(file_path)
        if blocks:
            lang_data[lang] = blocks
            
    if not lang_data:
        print("[Критична помилка] Не вдалося розпарсити жодного файлу. Аналіз скасовано.")
        return
        
    first_lang = list(lang_data.keys())[0]
    total_blocks = len(lang_data[first_lang])
    
    # Перевірка на однакову кількість блоків у базах даних
    for lang, blocks in lang_data.items():
        if len(blocks) != total_blocks:
            print(f"[Попередження] Увага! Мова '{lang}' має {len(blocks)} блоків, тоді як '{first_lang}' має {total_blocks}.")
            print("[!] Скрипт спробує виконати порівняння за мінімальним спільним індексом.")
            total_blocks = min(total_blocks, len(blocks))

    exclude_manifest = {
        "metadata": {
            "description": "Блоки, бінарний вміст яких 100% збігається в усіх знайдених мовах (системні константи)",
            "total_blocks_analyzed": total_blocks,
            "languages_analyzed": list(lang_data.keys())
        },
        "exclude_indices": [],
        "blocks": {}
    }
    
    print(f"\n[*] Порівняння {total_blocks} блоків за унікальними індексами...")
    match_count = 0
    
    for idx in range(total_blocks):
        ref_block = lang_data[first_lang][idx]
        ref_hex = ref_block["raw_hex"]
        ref_len = ref_block["length"]
        
        if ref_hex in ("", "00", "0000"):
            continue
            
        is_identical_everywhere = True
        for lang, blocks in lang_data.items():
            if idx >= len(blocks) or blocks[idx]["raw_hex"] != ref_hex:
                is_identical_everywhere = False
                break
                
        if is_identical_everywhere:
            match_count += 1
            preview = generate_preview(ref_hex)
            
            exclude_manifest["exclude_indices"].append(idx)
            exclude_manifest["blocks"][str(idx)] = {
                "length": ref_len,
                "raw_hex": ref_hex,
                "preview": preview
            }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(exclude_manifest, f, indent=4, ensure_ascii=False)
        
    print(f"\n[+] Аналіз завершено успішно!")
    print(f"[+] Проаналізовано мов: {len(lang_data)}")
    print(f"[+] Знайдено ідентичних системних блоків: {match_count}")
    print(f"[+] Список збережено у: {output_file.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Універсальний скрипт крос-мовного аналізу бінарних блоків Acronis .msg та генерації виключень.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "-b", "--base-dir",
        type=Path,
        default=Path("."),
        help="Базова директорія, де розташовані папки з мовними локалізаціями."
    )
    
    parser.add_argument(
        "-p", "--pattern",
        type=str,
        default="trueimg_home_pe_{lang}.msg",
        help="Шаблон імені файлу. Місце, де скрипт підставить ім'я мови, має містити {lang}."
    )
    
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("exclude_list.json"),
        help="Шлях та ім'я вихідного файлу JSON зі списком виключень."
    )
    
    parser.add_argument(
        "-l", "--languages",
        nargs="+",
        default=DEFAULT_LANGS,
        help="Список мовних папок для аналізу (можна вказати кілька через пробіл)."
    )

    args = parser.parse_args()
    
    analyze_exclusions(
        base_path=args.base_dir,
        languages=args.languages,
        pattern=args.pattern,
        output_file=args.output
    )


if __name__ == "__main__":
    main()